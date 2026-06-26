"""
Tests unitarios para la función sanitize_email en app.utils.email.

Responsabilidad:
- Verificar que todas las transformaciones de sanitización funcionan correctamente
- Validar comportamiento con casos borde (vacío, espacios solo)
- Asegurar consistencia del formato normalizado

Cómo funciona:
1. Cada test cubre un caso específico definido en los requisitos
2. Se verifica el resultado exacto esperado para cada transformación
3. Los tests son independientes y no comparten estado entre sí
"""
import pytest
from app.utils.email import sanitize_email


class TestSanitizeEmail:
    """Clase de pruebas para sanitización de emails."""
    
    @pytest.mark.parametrize(
        'input_email,expected',
        [
            ('  User@Example.COM  ', 'user@example.com'),
            ('TEST@DOMAIN.ORG', 'test@domain.org'),
            ('no-spaces@email.net', 'no-spaces@email.net'),
            ('   spaced @ email . com   ', 'spaced@email.com'),
        ]
    )
    def test_email_with_spaces_and_uppercase(self, input_email: str, expected: str) -> None:
        """
        Verifica que emails con espacios y mayúsculas se normalizan correctamente.
        
        Args:
            input_email: Email de entrada con variaciones de formato
            expected: Resultado esperado después de sanitización
        """
        result = sanitize_email(input_email)
        assert result == expected, f'Expected {expected!r} but got {result!r}'
    
    def test_already_normalized(self) -> None:
        """
        Verifica que un email ya normalizado no cambia.
        
        Este caso asegura idempotencia de la función.
        """
        input_email = 'already.normalized@email.com'
        result = sanitize_email(input_email)
        assert result == input_email
    
    def test_empty_string(self) -> None:
        """
        Verifica el comportamiento con string vacío.
        
        El resultado debe ser un string vacío, no raise exception.
        """
        input_email = ''
        result = sanitize_email(input_email)
        assert result == '', f'Expected empty string but got {result!r}'
    
    def test_only_spaces(self) -> None:
        """
        Verifica el comportamiento con solo espacios.
        
        Debe retornar un string vacío después de strip().
        """
        input_email = '   '
        result = sanitize_email(input_email)
        assert result == '', f'Expected empty string but got {result!r}'
    
    def test_multiple_internal_spaces(self) -> None:
        """
        Verifica que múltiples espacios internos se eliminan todos.
        
        El replace(' ', '') debe eliminar TODOS los espacios, no solo uno.
        """
        input_email = 'user  @   example . com'
        expected = 'user@example.com'
        result = sanitize_email(input_email)
        assert result == expected
    
    def test_type_error_on_non_string(self) -> None:
        """
        Verifica que se raise TypeError con input no string.
        
        La función debe validar el tipo de entrada antes de procesar.
        """
        with pytest.raises(TypeError):
            sanitize_email(12345)
        
        with pytest.raises(TypeError):
            sanitize_email(None)  # type: ignore[arg-type]
