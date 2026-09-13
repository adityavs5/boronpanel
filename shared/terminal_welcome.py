"""Plain-text terminal branding; never interpreted as shell code."""
from shared.validation import ValidationError

DEFAULT_TERMINAL_BANNER = '''BBBB    OOO   RRRR    OOO   N   N
B   B  O   O  R   R  O   O  NN  N
BBBB   O   O  RRRR   O   O  N N N
B   B  O   O  R  R   O   O  N  NN
BBBB    OOO   R   R   OOO   N   N'''


def validate_terminal_banner(value):
    if value is None:return None
    if not isinstance(value,str):raise ValidationError('Terminal banner must be plain text')
    value=value.replace('\r\n','\n')
    if len(value)>4000 or len(value.splitlines())>30:
        raise ValidationError('Terminal banner must fit within 30 lines and 4000 characters')
    if any(not (32<=ord(char)<=126 or char=='\n') for char in value):
        raise ValidationError('Use printable ASCII characters and line breaks for the terminal banner')
    return value


def render_terminal_banner(value=None):
    value=DEFAULT_TERMINAL_BANNER if value is None else validate_terminal_banner(value)
    return value.replace('\n','\r\n')+'\r\n\r\n' if value else ''
