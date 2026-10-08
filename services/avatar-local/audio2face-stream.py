import asyncio
import base64
import json
import os
import secrets
import time
import traceback
from pathlib import Path

import numpy as np
from aiohttp import web
import omni.graph.core as og
import omni.kit.app
import omni.kit.commands
import omni.usd
import omni.audio2face.core
import omni.audio2face.player
import omni.audio2face.tool
import omni.audio2face.exporter
from omni.audio2face.exporter.scripts.bsSolveUtils import add_blendshape_solve_pipe

if not os.environ.get('A2F_INSTALL_DIR'):
    raise RuntimeError('Set A2F_INSTALL_DIR to your separately installed Audio2Face 2023.2.')
APP = Path(os.environ['A2F_INSTALL_DIR']).resolve()
STATE = Path(os.environ.get('AVATAR_STATE_DIR', str(Path(__file__).resolve().parent / '.local'))).resolve() / 'stream'
STATE.mkdir(parents=True, exist_ok=True)
KEY_PATH = STATE / 'key.txt'
if not KEY_PATH.exists():
    KEY_PATH.write_text(secrets.token_urlsafe(48), encoding='ascii')
KEY = KEY_PATH.read_text(encoding='ascii').strip()
session = None
player = None
core = None
core_path = None
ready = False
busy = False


def report(stage, **values):
    (STATE / 'status.json').write_text(json.dumps({'stage': stage, **values}), encoding='utf-8')
    print('[A2F STREAM]', stage, values, flush=True)


@web.middleware
async def guard(request, handler):
    if request.headers.get('Origin') or not secrets.compare_digest(request.headers.get('X-Stream-Key', ''), KEY):
        raise web.HTTPForbidden()
    return await handler(request)


async def health(request):
    return web.json_response({'ready': ready, 'busy': busy, 'activeSession': session is not None, 'engine': 'nvidia-audio2face-2023.2'})


async def start(request):
    global session
    if not ready:
        raise web.HTTPServiceUnavailable()
    if busy or (session and time.monotonic() - session['updated'] < 120):
        raise web.HTTPConflict(text='Audio2Face is busy')
    player.get_player().pause()
    player._audio_start_callback(24000)
    core.set_track_ref(track_ref=player.get_player().get_track_ref())
    session = {'id': secrets.token_urlsafe(24), 'samples': 0, 'frame': 0, 'updated': time.monotonic()}
    return web.json_response({'session': session['id'], 'fps': 30, 'sampleRate': 24000})


async def chunk(request):
    global busy, session
    data = await request.json()
    if not session or data.get('session') != session['id']:
        raise web.HTTPConflict(text='Expired stream')
    if busy:
        raise web.HTTPConflict(text='Concurrent chunks are not allowed')
    if session['samples'] >= 24000 * 90:
        raise web.HTTPRequestEntityTooLarge(max_size=90, actual_size=session['samples'] / 24000)
    raw = base64.b64decode(data.get('pcm', ''), validate=True)
    if len(raw) > 48000 or len(raw) % 2:
        raise web.HTTPBadRequest(text='Invalid PCM chunk')
    final = data.get('final') is True
    pcm = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768
    session['samples'] += len(pcm)
    duration = session['samples'] / 24000
    # Keep future acoustic context available at chunk boundaries.
    if final:
        pcm = np.concatenate((pcm, np.zeros(7200, dtype=np.float32)))
    player.get_player().append_track_data(pcm, 1.0)
    target = duration if final else max(0, duration - .3)
    frames = []
    busy = True
    began = time.monotonic()
    try:
        while session['frame'] / 30 < target:
            if session.get('cancelled'):
                break
            t = session['frame'] / 30
            og.Controller.set(og.Controller.attribute(core_path + '.inputs:time'), t)
            og.Controller.evaluate_sync('/audio2face')
            names = list(og.Controller.attribute('/audio2face/BlendshapeSolve.outputs:out_blendshape_names').get())
            weights = [float(value) for value in og.Controller.attribute('/audio2face/BlendshapeSolve.outputs:out_weights').get()]
            if not names or len(names) != len(weights) or not np.isfinite(weights).all():
                raise RuntimeError('No valid NVIDIA solver output')
            frames.append({'t': t, 'weights': weights})
            session['frame'] += 1
        session['updated'] = time.monotonic()
        return web.json_response({'names': names if frames else [], 'frames': frames, 'duration': duration, 'processingMs': round((time.monotonic() - began) * 1000)})
    finally:
        busy = False
        if session and session.get('cancelled'):
            session = None


async def stop(request):
    global session
    data = await request.json()
    if session and (data.get('session') == session['id'] or data.get('reset') is True):
        session['cancelled'] = True
        if not busy:
            player.get_player().pause()
            session = None
    return web.json_response({'stopped': session is None})


async def prepare():
    global player, core, core_path, ready
    try:
        manager = omni.audio2face.player.get_ext().player_streaming_manager()
        # The bundled gRPC server otherwise listens on all interfaces.
        manager._streaming_server.shutdown()
        await omni.usd.get_context().new_stage_async()
        tool = omni.audio2face.tool.get_ext()
        mesh = tool.create_head_template('mark_skin').GetPrim()
        player_name, core_name = tool.create_a2f_pipeline('streaming', 'mark_fullface', mesh)
        report('initializing', player=player_name, core=core_name)
        await asyncio.wait_for(manager.wait_for_ready(player_name), 900)
        core_manager = omni.audio2face.core.get_ext().core_fullface_manager()
        await asyncio.wait_for(core_manager.wait_for_ready(core_name), 900)
        player = manager.get_instance(player_name)
        core = core_manager.get_instance(core_name)
        core_path = core_name
        core.set_setting_val('a2e_streaming_live_mode', False)
        og.Controller.disconnect(player_name + '.outputs:time', core_name + '.inputs:time')
        player.get_player().close()
        player.set_setting_val('dummy_player', True)
        player._init_player()
        bs_path = '/World/mark_bs_transfer_46'
        asset = APP / 'exts/omni.audio2face.exporter/deps/audio2face-blendshape-transfer/mark_bs_transfer_46.usd'
        omni.kit.commands.execute('CreateReference', usd_context=omni.usd.get_context(), path_to=bs_path, asset_path=str(asset))
        await omni.kit.app.get_app().next_update_async()
        if not add_blendshape_solve_pipe(str(mesh.GetPrimPath()), bs_path + '/neutral'):
            raise RuntimeError('Could not create NVIDIA blendshape solver')
        for _ in range(10):
            await omni.kit.app.get_app().next_update_async()
        app = web.Application(middlewares=[guard], client_max_size=100000)
        app.add_routes([web.get('/health', health), web.post('/start', start), web.post('/chunk', chunk), web.post('/stop', stop)])
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', 8012).start()
        ready = True
        report('ready', port=8012)
    except Exception as error:
        report('failed', error=str(error), traceback=traceback.format_exc())
        traceback.print_exc()


asyncio.ensure_future(prepare())
