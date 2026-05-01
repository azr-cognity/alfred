\# Cognity Local Development Environment



Estructura local de desarrollo para Cognity.



\## Carpetas



\- `products/`: proyectos formales.

\- `labs/`: pruebas, prototipos y experimentos.

\- `agent-workspaces/`: carpetas donde pueden trabajar agentes.

\- `models/`: modelos locales.

\- `datasets/`: datos de prueba y datasets sintéticos.

\- `backups/`: respaldos.

\- `secure-core/`: información sensible. No debe ser tocada por agentes.



\## Regla de seguridad



Los agentes nunca deben trabajar directamente en `secure-core` ni en ramas `main`.

