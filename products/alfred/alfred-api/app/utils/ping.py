"""
Utilidad de ping para verificación básica del sistema.

Responsabilidad:
  Proporcionar una función simple que devuelve 'pong' como respuesta al comando 'ping'.
  Útil para health checks y pruebas básicas de conectividad.

Uso:
    from app.utils.ping import ping
    
    response = ping()
    assert response == "pong"
"""


def ping() -> str:
    """
    Devuelve el string literal 'pong' como respuesta al comando ping.

    Returns:
        El string 'pong' que confirma la funcionalidad básica del sistema.

    Raises:
        No raises — esta función no tiene side effects ni puede fallar.
    """
    return "pong"