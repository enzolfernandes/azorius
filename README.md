# Azorius — Juiz de Magic: The Gathering (Nível 3)

Protótipo local de um juiz de regras de MTG com arquitetura RAG (Retrieval-Augmented Generation):
as Regras Abrangentes oficiais são indexadas em um banco vetorial (ChromaDB), os dados exatos das
cartas vêm da API do Scryfall e a resposta final é gerada por um LLM (Gemini, OpenAI ou Claude)
usando apenas o contexto recuperado — sem alucinações.

## Arquitetura

- `app.py` — interface Streamlit (exclusivamente UI; nenhuma lógica de negócio).
- `services/` — módulos puros, sem nenhuma importação de Streamlit:
  - `config.py` — validação de provedor/chave (UI ou `.env`).
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

2. (Opcional) Preencha o `.env` para uso solo — a sidebar da app também aceita provedor e chave:

   ```
   LLM_PROVIDER=gemini        # ou "openai" ou "claude"
   GOOGLE_API_KEY=...         # se gemini
   OPENAI_API_KEY=...         # se openai
   ANTHROPIC_API_KEY=...      # se claude
   ```

   Com Claude, o chat usa a API Anthropic; os embeddings do RAG são locais (MiniLM via
   Chroma), num índice separado — a primeira ingestão desse provedor pode demorar mais.

3. Baixe as Regras Abrangentes oficiais:

   ```bash
   python scripts/setup_rules.py
   ```

4. Rode a aplicação (a primeira execução por provedor ingere as regras no ChromaDB, o que leva alguns minutos):

   ```bash
   streamlit run app.py
   ```

5. Na barra lateral, escolha o provedor (Gemini, OpenAI ou Claude), cole a chave de API e clique em **Aplicar**.

## Compartilhar com pessoas próximas

Cada visitante usa a **própria** chave na sidebar (não compartilhe a sua no `.env` do host).

### Na mesma rede (LAN)

No computador host:

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

Descubra o IP local do host (ex.: `ipconfig` no Windows) e acesse no outro dispositivo:

```
http://192.168.x.x:8501
```

### Fora da rede (tunnel)

Exponha a porta 8501 com um tunnel, por exemplo:

- [ngrok](https://ngrok.com/): `ngrok http 8501`
- [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/): `cloudflared tunnel --url http://localhost:8501`

Envie a URL gerada. O host precisa ter rodado `scripts/setup_rules.py` antes; a primeira abertura por provedor (Gemini vs OpenAI) pode demorar pela ingestão do Chroma.

## Uso

Digite sua dúvida no chat citando as cartas pelo nome, naturalmente:

> Se eu controlo Doubling Season e uso a habilidade -2 da Vraska, Golgari Queen, quantas lealdades ela perde?

O sistema identifica as cartas via LLM (aceita apelidos e nomes em português, traduzindo para o
nome oficial em inglês). Se quiser forçar um nome exato, use [[colchetes duplos]] — eles têm
prioridade e pulam a etapa de identificação.

As imagens das cartas citadas aparecem na barra lateral e a resposta do juiz cita os números
das regras oficiais usadas.
