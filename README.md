# Sistema de Gerenciamento Solar e Bateria

Sistema unificado para monitoramento de geração solar e gerenciamento de bateria, oferecendo interface API REST e CLI para consultas em linguagem natural.

## Funcionalidades

**Monitoramento Solar:**
- Consulta de dados históricos de geração
- Análise estatística de desempenho
- Processamento de dados em formato CSV

**Gerenciamento de Bateria:**
- Monitoramento de status em tempo real
- Controle de fluxo de energia
- Gerenciamento dinâmico de destinos

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

**Interface CLI:**
```bash
python cli.py
```

## Endpoints da API

**Chat e Interface Principal:**
- `POST /chat` - Interface de linguagem natural
- `POST /command` - Endpoint legado

**Geração Solar:**
- `POST /solar/query` - Consultas de geração
- `GET /solar/stats` - Estatísticas gerais

**Bateria:**
- `GET /battery/status` - Status atual
- `GET /battery/energy-flow` - Fluxo de energia
- `POST /battery/add-destinations` - Adicionar destinos
- `POST /battery/remove-destinations` - Remover destinos

**Sistema:**
- `GET /health` - Status do sistema
- `GET /` - Visão geral da API

## Estrutura do Projeto

```
├── main.py                # Aplicação FastAPI principal
├── cli.py                 # Interface CLI
├── core/
│   ├── gemini.py          # Integração Gemini AI
│   ├── solar_tools.py     # Processamento dados solares
│   └── battery.py         # Gerenciamento bateria
├── api/
│   └── endpoints.py       # Endpoints da API
├── solar_generation.csv   # Dados de amostra
├── system_prompt.txt      # Configuração AI
└── requirements.txt       # Dependências
```

## Tecnologias

- **Google Gemini 2.0 Flash** - Processamento linguagem natural
- **FastAPI** - Framework web moderno
- **Pandas** - Processamento de dados
- **Function Calling** - Integração estruturada com IA

## Configuração de Dados

**Formato CSV (solar_generation.csv):**
```csv
date,energy_kwh
2025-01-01,26.99
2025-01-02,40.82
```

**Personalização:**
- Editar `system_prompt.txt` para modificar comportamento da IA
- Substituir dados CSV pelos dados reais do sistema
- Configurar variáveis de ambiente conforme necessário
