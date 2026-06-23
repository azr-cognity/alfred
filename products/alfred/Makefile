# Makefile — comandos para operar Alfred
# Uso: make <comando>
# Ejemplo: make up

.PHONY: up down reset logs db-shell test-opa test-ollama

# Levanta todos los servicios en background
up:
	docker compose up -d
	@echo ""
	@echo "Alfred infra corriendo:"
	@echo "  Postgres  → localhost:5432"
	@echo "  OPA       → localhost:8181"
	@echo "  pgAdmin   → http://localhost:5050"
	@echo "  Redis     → localhost:6379"
	@echo "  Ollama    → http://localhost:11434 (host)"

# Para todos los servicios
down:
	docker compose down

# Borra todo incluyendo datos (cuidado — borra la DB)
reset:
	docker compose down -v
	@echo "Todos los volúmenes eliminados. La DB se recreará al hacer 'make up'."

# Ver logs en tiempo real de todos los servicios
logs:
	docker compose logs -f

# Ver logs de un servicio específico: make logs-db
logs-%:
	docker compose logs -f $*

# Abrir una shell en Postgres (para explorar la DB desde terminal)
db-shell:
	docker compose exec postgres psql -U alfred -d alfred_db

# Verificar que OPA está corriendo y la política de Alfred carga bien
test-opa:
	@echo "Probando OPA..."
	@curl -s http://localhost:8181/health | python3 -m json.tool
	@echo ""
	@echo "Políticas cargadas:"
	@curl -s http://localhost:8181/v1/policies | python3 -m json.tool

# Verificar que Ollama responde con el modelo correcto
test-ollama:
	@echo "Modelos disponibles en Ollama:"
	@curl -s http://localhost:11434/api/tags | python3 -c "import sys,json; [print(' -', m['name']) for m in json.load(sys.stdin)['models']]"
	@echo ""
	@echo "Test rápido con qwen3.5:35b-a3b..."
	@curl -s http://localhost:11434/api/generate \
		-d '{"model":"qwen3.5:35b-a3b","prompt":"responde solo: OK","stream":false}' \
		| python3 -c "import sys,json; print('Respuesta:', json.load(sys.stdin)['response'])"

# Test end-to-end: OPA evalúa un output de ejemplo del Coder
test-policy:
	@echo "Enviando output de ejemplo a OPA..."
	@curl -s -X POST http://localhost:8181/v1/data/alfred/coder/deny \
		-H "Content-Type: application/json" \
		-d '{"input": {"files": [{"path": "api/routes.py", "language": "python", "content": "print(\"hola\")\ndef test(): pass"}]}}' \
		| python3 -m json.tool
