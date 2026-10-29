import hashlib
import json
import urllib.request
from pathlib import Path

root = Path.home() / "eduai-a2f/models/mark"
root.mkdir(parents=True, exist_ok=True)
repository = "nvidia/Audio2Face-3D-v2.3-Mark"
with urllib.request.urlopen("https://huggingface.co/api/models/" + repository, timeout=30) as response:
    revision = json.load(response)["sha"]
with urllib.request.urlopen("https://huggingface.co/api/models/" + repository + "/tree/" + revision + "?recursive=true&expand=false", timeout=30) as response:
    files = {item["path"]: item for item in json.load(response) if item["type"] == "file"}
required = ["model.json", "network_info.json", "network.onnx", "model_data.npz", "bs_skin.npz", "bs_skin_config.json", "bs_tongue.npz", "bs_tongue_config.json", "implicit_emo_db.npz", "model_config.json", "trt_info.json", "README.md"]
receipt = {"repository": repository, "revision": revision, "files": {}}
for name in required:
    info = files[name]
    if info["size"] > 512 * 1024 * 1024:
        raise RuntimeError("Unexpected model size")
    destination = root / name
    if not destination.exists() or destination.stat().st_size != info["size"]:
        temporary = root / (name + ".partial")
        with urllib.request.urlopen("https://huggingface.co/" + repository + "/resolve/" + revision + "/" + name, timeout=120) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        if temporary.stat().st_size != info["size"]:
            raise RuntimeError("Incomplete download: " + name)
        temporary.replace(destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    expected = info.get("lfs", {}).get("oid")
    if expected and digest != expected:
        raise RuntimeError("Model checksum mismatch: " + name)
    receipt["files"][name] = {"bytes": destination.stat().st_size, "sha256": digest}
    print(name, destination.stat().st_size, flush=True)
(root / "download-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
