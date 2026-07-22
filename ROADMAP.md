# Roadmap — Azorius

Backlog e status. Itens `[x]` já estão no código.

## Deckbuilder

- [x] **Lista em blocos na montagem** — `# Header` + `Nx Nome` → `st.expander` (`services/decklist_parse.py`).
- [x] **Geração de deck rápida** — autopilot via `build_autopilot_deck` + frase opcional do LLM.
- [x] **Modo de melhoria de deck** — colar lista → gaps (`deck_upgrade.py`) → prompt dedicado.
- [x] **Preços via LigaMagic** — `ligamagic_prices.py` (BRL) em todos os modos do Deckbuilder; sem fallback USD no orçamento (mercado Brasil primeiro).

## UX / interface

- [x] **Histórico de conversas** — JSON em `data/conversations/`; nova / retomar / apagar na sidebar (Juiz e Deckbuilder separados).
- [x] **Repetir / próxima** — botão que reenvia a última mensagem do usuário.
- [x] **Configurações em dialog** — botão na sidebar abre `st.dialog` (provedor + chave); radio de modo só Juiz | Deckbuilder.
- [x] **Preview estilo LigaMagic** — nomes marcados nas listas/`[[menções]]`; imagem flutuante no hover dentro do chat (`st.html` + Scryfall cacheado).

## Notas

- Manter isolamento: UI em `app.py`, lógica em `services/`.
- Juiz (RAG) e Deckbuilder permanecem desacoplados; refinamentos de UX não devem alterar o pipeline de regras.
