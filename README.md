# Azorius — meu assistente de regras de Magic: The Gathering

Desenvolvo este protótipo em Python para consultar regras e cartas de Magic: The Gathering em uma interface Streamlit. Combino recuperação de regras em ChromaDB, dados de cartas da API Scryfall e geração de respostas com um provedor de LLM configurável.

## Como organizei o projeto

| Camada | Arquivos · Função |
| --- | --- |
| Interface | [app.py](app.py) · Chat, seleção de provedor, histórico de cartas e exibição das respostas |
| Configuração e provedores | [config.py](services/config.py), [providers.py](services/providers.py) · Configurações e interface comum para Gemini, OpenAI e Claude |
| Regras | [rules_setup.py](services/rules_setup.py), [vector_db.py](services/vector_db.py) · Download das Comprehensive Rules, divisão por regras numeradas e índice vetorial persistente |
| Cartas | [scryfall_api.py](services/scryfall_api.py) · Consulta de nomes, texto das cartas, imagens e rulings |
| Respostas | [llm_engine.py](services/llm_engine.py) · Extração de nomes, reescrita da consulta, contexto e resposta em streaming |

Meu histórico registra a [separação da configuração e dos provedores](https://github.com/enzolfernandes/azorius/commit/2c2d69c830689bec80dbc3a85196ac3fe167951f) e o [tratamento do histórico de cartas e das consultas](https://github.com/enzolfernandes/azorius/commit/f7b5f69a6665e9bb5e5d37f2921c58a9713a3cba). Os SDKs, modelos, ChromaDB e dados consultados são serviços e dependências externos.

## Preparar e executar localmente

1. Clone o repositório e entre na pasta:

   ```shell
   git clone https://github.com/enzolfernandes/azorius.git
   cd azorius
   python -m venv .venv
   ```

2. Ative o ambiente virtual (`.venv\Scripts\Activate.ps1` no PowerShell ou `source .venv/bin/activate` em bash) e instale:

   ```shell
   python -m pip install -r requirements.txt
   ```

3. Configure um provedor e a respectiva chave na barra lateral. Para uso individual, o código também lê um `.env` local:

   ```dotenv
   LLM_PROVIDER=gemini
   GOOGLE_API_KEY=SUBSTITUA_LOCALMENTE
   # Alternativas: LLM_PROVIDER=openai com OPENAI_API_KEY
   # ou LLM_PROVIDER=claude com ANTHROP_API_KEY
   ```

4. Inicie a interface:

   ```shell
   streamlit run app.py
   ```

As regras são baixadas quando faltam em `data/MagicCompRules.txt`; o comando manual é `python scripts/setup_rules.py`. A primeira ingestão cria o índice por provedor. O caminho padrão é `data/chroma/${PROVEDOR}`; em caminhos com caracteres não ASCII no Windows, a configuração usa um diretório alternativo local. Claude usa a função de embeddings local do Chroma; os demais provedores usam suas APIs de embeddings.

## Usar

Escreva a dúvida citando as cartas pelo nome. Para indicar um nome explicitamente, use `[[Nome da Carta]]`. A interface mantém o histórico de cartas e apresenta a resposta com referências às regras recuperadas.

## Estado e limites

O repositório contém a implementação do protótipo. Esta revisão de documentação não executou a aplicação, não consumiu APIs e não mediu a qualidade das respostas. A recuperação de contexto ajuda a fundamentar as respostas, mas não garante correção: confira a regra citada e sua versão. "Juiz" é o papel da interface, não uma certificação de arbitragem.

O `requirements.txt` não fixa versões, e a disponibilidade dos modelos depende do provedor. O índice existente pode conservar conteúdo anterior quando o texto das regras muda; revisar atualização e reconstrução do índice faz parte da manutenção. Não há suíte de testes ou relatório de avaliação no estado inspecionado.

Para compartilhamento, revise autenticação, custos e tratamento de chaves antes de expor o app: o cache do índice é por provedor e pode usar a chave da primeira sessão que o inicializa. Uma chave digitada na interface é processada pelo servidor do app. As antigas instruções de hospedagem eram possibilidades de implantação, não prova de uma demo publicada. A branch atual é `master`.

## Fontes e autoria

- [Comprehensive Rules — Wizards of the Coast](https://magic.wizards.com/en/rules)
- [API Scryfall](https://scryfall.com/docs/api)
- [Dependências do projeto](requirements.txt)
- [Meu histórico de alterações](https://github.com/enzolfernandes/azorius/commits/master/)

Magic: The Gathering, regras e imagens de cartas pertencem aos respectivos titulares. O repositório não contém uma licença geral declarada para o código.

*Apresentação revisada em 17/10/2026; implementação preservada.*
