"""Safe operational diagnostics: no exception strings, locals or source text."""
import secrets
from pathlib import Path


class OperationFailure(RuntimeError):
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        self.reference = diagnostic['reference']
        super().__init__(f"internal operation failure (reference: {self.reference})")


def describe_failure(op, exception):
    root = Path(__file__).resolve().parent.parent
    frames = []
    error = exception
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        tb = error.__traceback__
        while tb:
            try:
                filename = Path(tb.tb_frame.f_code.co_filename).resolve().relative_to(root)
                if filename.parts[0] in ('daemon', 'shared', 'scripts', 'api'):
                    frames.append(f'{filename}:{tb.tb_lineno} ({tb.tb_frame.f_code.co_name})')
            except ValueError:
                pass
            tb = tb.tb_next
        error = error.__cause__
    result = {'reference': secrets.token_hex(8), 'operation': op,
              'error_type': type(exception).__name__, 'frames': frames[-12:]}
    if isinstance(exception, OSError) and isinstance(exception.errno, int):
        result['errno'] = exception.errno
    returncode = getattr(exception, 'returncode', None)
    if isinstance(returncode, int):
        result['returncode'] = returncode
    return result
