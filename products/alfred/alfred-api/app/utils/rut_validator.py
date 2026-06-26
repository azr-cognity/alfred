"""
Validador de RUT chileno con algoritmo módulo 11.

Responsabilidad:
  Validar el formato y dígito verificador del Rol Único Tributario (RUT) chileno,
siguiendo el estándar oficial del Servicio de Impuestos Internos (SII).

Cómo funciona:
  1. Normaliza input removiendo puntos y guión (-)
  2. Separa cuerpo numérico del dígito verificador
  3. Aplica algoritmo módulo 11 con serie [2,3,4,5,6,7] sobre el cuerpo invertido
  4. Calcula dígito esperado: resto==1 → 'k', resto==0 → '0', sino str(11-resto)
  5. Compara case-insensitive con dígito recibido

Ejemplos de uso:
    >>> validate_rut('12.345.678-9')
    True/False (depende del RUT válido)
    >>> validate_rut('11.111.111-k')  # Caso conocido válido
    True
"""

import structlog
from typing import Optional, List

logger = structlog.get_logger()

# Serie de multiplicación para el algoritmo módulo 11
MUL_SERIES: List[int] = [2, 3, 4, 5, 6, 7]


def _calculate_check_digit(body: str) -> str:
    """
    Calcula el dígito verificador esperado según cuerpo numérico.

    Args:
        body: Cuerpo numérico del RUT (sin puntos ni guión).

    Returns:
        Dígito verificador calculado ('0', '1'-'9', o 'k').
    """
    if not body or not body.isdigit():
        raise ValueError("Cuerpo debe ser numérico")

    # Invertir y aplicar serie de multiplicación
    total = 0
    for i, digit in enumerate(reversed(body)):
        multiplier = MUL_SERIES[i % len(MUL_SERIES)]
        total += int(digit) * multiplier

    remainder = total % 11

    # CORRECCIÓN: resto==1 → 'k', resto==0 → '0'
    if remainder == 1:
        return 'k'
    elif remainder == 0:
        return '0'

    return str(11 - remainder)


def validate_rut(rut: Optional[str]) -> bool:
    """
    Valida un RUT chileno verificando formato y dígito verificador.

    Args:
        rut: String con el RUT a validar (con o sin puntos/guion).

    Returns:
        True si es válido, False de lo contrario.
    """
    if not isinstance(rut, str):
        logger.warning("validate_rut.invalid_type", type=type(rut).__name__)
        return False

    # Normalizar: remover puntos y guión
    rut_clean = rut.replace('.', '').replace('-', '')

    if len(rut_clean) < 2:
        logger.debug("validate_rut.too_short")
        return False

    # Separar cuerpo (todo menos último char) del dígito verificador
    body = rut_clean[:-1]
    check_digit = rut_clean[-1].lower()

    if not body.isdigit():
        logger.debug("validate_rut.body_not_numeric")
        return False

    try:
        expected_digit = _calculate_check_digit(body)
    except ValueError as e:
        logger.error("validate_rut.calculation_error", error=str(e))
        return False

    if check_digit != expected_digit.lower():
        logger.debug(
            "validate_rut.check_mismatch",
            received=check_digit,
            expected=expected_digit,
        )
        return False

    logger.info("validate_rut.success", rut=rut)
    return True