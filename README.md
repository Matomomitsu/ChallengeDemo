# BotSolar — Integração GoodWe 

Sistema unificado para gerenciamento de bateria e alarmes via GoodWe SEMS, com API REST e CLI em linguagem natural.

## Funcionalidades

**Monitoramento/Alarmes (GoodWe):**
- Consulta de alarmes por intervalo de datas (aberto por planta)
- Detalhamento de alertas com traduções

**Gerenciamento de Bateria:**
- Monitoramento de status em tempo real (GoodWe)

**Interfaces:**
- API REST com FastAPI e documentação automática
- Interface CLI interativa

## Instalação

**Requisitos:**
- Python 3.9+
- Chave API Google Gemini

**Configuração:**
```bash
pip install -r requirements.txt
```

Criar arquivo `.env` na raiz:
```
GEMINI_API_KEY=sua_chave_api_aqui
```

## Uso

**Servidor API:**
```bash
python main.py
```
- Servidor: `http://localhost:8001`
- Documentação: `http://localhost:8001/docs`
- Web Demo: `http://localhost:8001/demo`

**Interface CLI:**
```bash
python cli.py
```

## Endpoints da API

**Chat e Interface Principal:**
- `POST /chat` - Interface de linguagem natural
- `POST /command` - Endpoint legado

Removidos: endpoints de CSV de geração solar.

**Bateria:**
- `GET /battery/status` - Status atual (GoodWe)

**Sistema:**
- `GET /health` - Status do sistema
- `GET /` - Visão geral da API

## Estrutura do Projeto

```
├── main.py                # Aplicação FastAPI principal
├── cli.py                 # Interface CLI
├── core/
│   ├── gemini.py          # Integração Gemini AI (function calling)
│   ├── goodweApi.py       # Integração GoodWe SEMS (token, plantas, SOC, alarmes)
├── api/
│   └── endpoints.py       # Endpoints da API
└── (removido) solar_generation.csv
├── system_prompt.txt      # Configuração AI
└── requirements.txt       # Dependências
```

## Tecnologias

- **Google Gemini 2.5 Flash** - Processamento linguagem natural
- **FastAPI** - Framework web moderno
- **GoodWe SEMS** - Fonte de dados de bateria e alarmes
- **Function Calling** - Integração estruturada com IA

## Configuração

Crie `.env` com:
```
GEMINI_API_KEY=...
GOODWE_ACCOUNT=...
GOODWE_PASSWORD=...
# Planta padrão (opcional; se não informar ID, usa por nome)
DEFAULT_POWERSTATION_NAME=Bauer
# Opcional: defina diretamente o ID
# DEFAULT_POWERSTATION_ID=6ef62eb2-7959-4c49-ad0a-0ce75565023a
```

Personalização:
- Edite `system_prompt.txt` para ajustar o comportamento da IA
- Configure as variáveis de ambiente conforme necessário
