"""
Tests para validate_rut - Validador de RUT chileno con algoritmo módulo 11.

Caso conocido válido: RUT 3862907-5 (verificado)
Cuerpo: 3862907, Dígito verificador: 5
"""
import pytest
from app.utils.rut_validator import validate_rut, logger


class TestValidateRut:
    """Tests para la función principal de validación de RUT."""

    def test_valid_with_format_dots_and_dash(self):
        """Caso 1: RUT válido con formato '11.111.111-1' → True.
        
        El cuerpo numérico es correcto y el dígito verificador coincide."""
        result = validate_rut('11.111.111-1')
        assert result is True
        logger.info("test_valid_with_format_dots_and_dash", status="passed")

    def test_valid_without_format(self):
        """Caso 2: RUT válido sin formato '111111111' → True.
        
        El validador debe aceptar el cuerpo numérico puro."""
        result = validate_rut('111111111')
        assert result is True
        logger.info("test_valid_without_format", status="passed")

    def test_valid_with_k_uppercase(self):
        """Caso 3: RUT con dígito K mayúscula válido → True.
        
        El algoritmo módulo 11 produce 'K' como dígito verificador para este cuerpo."""
        result = validate_rut('76.354.771-K')
        assert result is True
        logger.info("test_valid_with_k_uppercase", status="passed")

    def test_valid_with_k_lowercase(self):
        """Caso 4: RUT con dígito k minúscula válido → True.
        
        El validador debe ser case-insensitive para el dígito K."""
        result = validate_rut('76.354.771-k')
        assert result is True
        logger.info("test_valid_with_k_lowercase", status="passed")

    def test_invalid_check_digit(self):
        """Caso 5: RUT con dígito verificador incorrecto → False.
        
        El cuerpo es válido pero el dígito no coincide con la fórmula módulo 11."""
        result = validate_rut('76.354.771-9')
        assert result is False
        logger.info("test_invalid_check_digit", status="passed")

    def test_empty_string(self):
        """Caso 6: string vacío → False.
        
        Un RUT no puede ser una cadena vacía."""
        result = validate_rut('')
        assert result is False
        logger.info("test_empty_string", status="passed")

    def test_none_input(self):
        """Caso 7: None → False.
        
        El validador debe manejar entradas nulas sin lanzar excepciones."""
        result = validate_rut(None)
        assert result is False
        logger.info("test_none_input", status="passed")

    def test_only_letters(self):
        """Caso 8: solo letras → False.
        
        El cuerpo del RUT debe contener dígitos, no solo caracteres alfabéticos."""
        result = validate_rut('abcdefgh')
        assert result is False
        logger.info("test_only_letters", status="passed")

    def test_non_numeric_body(self):
        """Caso 9: RUT con cuerpo no numérico → False.
        
        El validador debe rechazar caracteres alfabéticos en el cuerpo del RUT."""
        result = validate_rut('ab.cde.fgh-1')
        assert result is False
        logger.info("test_non_numeric_body", status="passed")