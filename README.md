# Azorius — meu assistente de regras de Magic: The Gathering

Desenvolvo este protótipo em Python para consultar regras e cartas de Magic: The Gathering em uma interface Streamlit. Combino recuperação de regras em ChromaDB, dados da API Scryfall e respostas de um provedor de LLM configurável.

## Como organizei o projeto

| Componente | Implementação |
| --- | --- |
| Interface de chat e histórico de cartas | [app.py](app.py) |
| Configuração e interface comum de provedores | [config.py](services/config.py), [providers.py](services/providers.py) |
| Download de regras e índice vetorial | [rules_setup.py](services/rules_setup.py), [vector_db.py](services/vector_db.py) |
| Consulta de cartas e rulings | [scryfall_api.py](services/scryfall_api.py) |
| Contexto, consultas e respostas em streaming | [llm_engine.py](services/llm_engine.py) |

## Preparar e executar

No Windows, com Python e Git disponíveis:

```shell
git clone https://github.com/enzolfernandes/azorius.git
cd azorius
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Em Linux/macOS, use `.venv/bin/python` no lugar do executável do ambiente virtual do Windows.

Na barra lateral, selecione Gemini, OpenAI ou Claude e informe a chave do provedor. Para configurar um `.env` local, consulte os nomes das variáveis diretamente em [services/config.py](services/config.py). Mantenha as chaves fora do repositório.

As regras são baixadas quando faltam em `data/MagicCompRules.txt`; o comando manual é `python scripts/setup_rules.py`. O índice é organizado por provedor sob `data/chroma/`, com caminho alternativo local no Windows quando necessário. Confira a configuração antes de reutilizar um índice existente.

## Usar e conferir

Escreva a dúvida citando as cartas pelo nome. A notação `[[Nome da Carta]]` permite indicar um nome explicitamente. A interface mantém o histórico de cartas e apresenta referências às regras recuperadas. Confira a regra citada e sua versão ao avaliar a resposta.

## Minha contribuição e estado

Meu histórico registra a [separação da configuração e dos provedores](https://github.com/enzolfernandes/azorius/commit/2c2d69c830689bec80dbc3a85196ac3fe167951f) e o [tratamento do histórico de cartas e consultas](https://github.com/enzolfernandes/azorius/commit/f7b5f69a6665e9bb5e5d37f2921c58a9713a3cba). Os SDKs, modelos, ChromaDB e dados consultados são dependências e serviços externos.

O projeto é um protótipo. A recuperação de contexto ajuda a fundamentar as respostas, mas não garante correção. As dependências não têm versões fixadas no manifesto, e a disponibilidade dos modelos depende do provedor. A atualização das regras também exige conferir ou reconstruir o índice. Não há relatório de avaliação ou suíte de testes no estado inspecionado.

Revisão documental em 17/10/2026: não executei a aplicação nem medi a qualidade das respostas nesta revisão.
