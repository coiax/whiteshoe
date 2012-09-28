class WhiteshoeBaseException(Exception):
    pass

class NewScene(WhiteshoeBaseException):
    pass

class CloseProgram(WhiteshoeBaseException):
    pass

class PlayerNotFound(WhiteshoeBaseException):
    pass
