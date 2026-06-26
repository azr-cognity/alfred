"""
Utilidades para manejo y sanitización de correos electrónicos.

Responsabilidad:
- Proporcionar funciones para normalizar direcciones de email antes de su uso en el sistema
- Aplicar transformaciones consistentes: strip, remove spaces, lowercase

Cómo funciona:
1. sanitize_email() aplica tres pasos secuenciales a cualquier string recibido
2. Los espacios se eliminan completamente (inicio/fin y internos)
3. El resultado siempre está normalizado para comparación segura de emails
"""
import structlog
from typing import Union

logger = structlog.get_logger()

def sanitize_email(email: str) -> str:
    """
    Sanitiza una dirección de correo electrónico.
    
    Aplica tres transformaciones en secuencia:
    1. strip() - elimina espacios al inicio y final
    2. replace(' ', '') - elimina todos los espacios internos
    3. lower() - convierte todo a minúsculas
    
    Args:
        email: String que representa una dirección de correo electrónico.
               Puede contener mayúsculas, espacios o caracteres irregulares.
    
    Returns:
        Email sanitizado en formato normalizado (minúsculas sin espacios).
    
    Examples:
        >>> sanitize_email('  User@Example.COM  ')
        'user@example.com'
        >>> sanitize_email('TEST@DOMAIN.ORG')
        'test@domain.org'
        >>> sanitize_email('no-spaces@email.net')
        'no-spaces@email.net'
    
    Raises:
        TypeError: Si el input no es un string
    """
    if not isinstance(email, str):
        logger.error("email.sanitize_invalid_type", type=type(email).__name__)
        raise TypeError(f"Expected str but got {type(email).__name__}")
    
    sanitized = email.strip().replace(' ', '').lower()
    
    if not sanitized:
        logger.warning("email.empty_after_sanitize")
    else:
        logger.debug("email.sanitized", original=email, sanitized=sanitized)
    
    return sanitized
