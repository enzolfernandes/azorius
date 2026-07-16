# Azorius — Juiz de Magic: The Gathering (Nível 3)

Protótipo local de um juiz de regras de MTG com arquitetura RAG (Retrieval-Augmented Generation):
as Regras Abrangentes oficiais são indexadas em um banco vetorial (ChromaDB), os dados exatos das
cartas vêm da API do Scryfall e a resposta final é gerada por um LLM (Gemini ou OpenAI) usando
apenas o contexto recuperado — sem alucinações.

## Arquitetura

- `app.py` — interface Streamlit (exclusivamente UI; nenhuma lógica de negócio).
- `services/` — módulos puros, sem nenhuma importação de Streamlit:
  - `config.py` — carregamento e validação do `.env`.
  - `providers.py` — factory de provedores (LLM + embeddings), ponto único de injeção de dependência.
  - `scryfall_api.py` — consulta de cartas na API do Scryfall.
  - `vector_db.py` — chunking das regras e persistência/consulta no ChromaDB.
  - `llm_engine.py` — montagem do prompt de juiz e geração em streaming.
- `scripts/setup_rules.py` — download automático do arquivo oficial de Regras Abrangentes (.txt).
- `data/` — regras baixadas e banco vetorial persistente (gerados localmente).

## Setup

1. Instale as dependências:

   ```bash
   pip install -r requirements.txt
   ```

2. Preencha o `.env` com o provedor desejado e a chave de API correspondente:

   ```
   LLM_PROVIDER=gemini        # ou "openai"
   GOOGLE_API_KEY=...         # se gemini
   OPENAI_API_KEY=...         # se openai
   ```

3. Baixe as Regras Abrangentes oficiais:

   ```bash
   python scripts/setup_rules.py
   ```

4. Rode a aplicação (a primeira execução ingere as regras no ChromaDB, o que leva alguns minutos):

   ```bash
   streamlit run app.py
   ```

## Uso

Digite sua dúvida no chat citando cartas entre colchetes duplos, por exemplo:

> Se eu controlo [[Doubling Season]] e uso a habilidade -2 do [[Vraska, Golgari Queen]], quantas lealdades ela perde?

As imagens das cartas citadas aparecem na barra lateral e a resposta do juiz cita os números
das regras oficiais usadas.
