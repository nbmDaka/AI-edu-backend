import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, close_old_connections
from apps.activities.workspace.worker import claim_next_operation, execute_operation, recover_expired_operations
from apps.activities.workspace.diagnostics import mark_stage, log_operation_error


class Command(BaseCommand):
    help = 'Process workspace AI operations; PostgreSQL is required for continuous/concurrent workers.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Recover leases and process at most one operation, then exit')
        parser.add_argument('--poll-interval', type=float, default=2.0)
        parser.add_argument('--concurrency', type=int, default=10, help='Maximum parallel operations (1-10)')

    def handle(self, *args, **options):
        if not 0.1 <= options['poll_interval'] <= 30:
            raise CommandError('--poll-interval must be between 0.1 and 30 seconds')
        if connection.vendor != 'postgresql' and not options['once']:
            raise CommandError('Continuous workers require PostgreSQL; use --once for isolated SQLite development')
        if not 1 <= options['concurrency'] <= 10:
            raise CommandError('--concurrency must be between 1 and 10')

        def process(operation):
            close_old_connections()
            try:
                execute_operation(operation)
            except Exception as error:
                mark_stage(operation, 'worker')
                log_operation_error(operation, 'worker_error', error=error)
                raise CommandError('Workspace worker failed; see error diagnostics') from None
            finally:
                # Each worker thread owns a separate Django connection.
                from django.db import connections
                connections.close_all()

        if options['once']:
            recover_expired_operations()
            operation = claim_next_operation()
            if operation:
                process(operation)
            return
        try:
            with ThreadPoolExecutor(max_workers=options['concurrency']) as executor:
                pending = set()
                while True:
                    close_old_connections()
                    recover_expired_operations()
                    while len(pending) < options['concurrency']:
                        operation = claim_next_operation()
                        if operation is None:
                            break
                        pending.add(executor.submit(process, operation))
                    if pending:
                        completed, pending = wait(pending, timeout=options['poll_interval'], return_when=FIRST_COMPLETED)
                        for future in completed:
                            future.exception()  # process() already logged any unexpected failure.
                    else:
                        time.sleep(options['poll_interval'])
        except KeyboardInterrupt:
            pass
