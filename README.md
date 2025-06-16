# BotSolar - Sistema Unificado de Gerenciamento Solar e Bateria

Um sistema abrangente que combina monitoramento de geração solar com capacidades de gerenciamento de bateria. Este projeto une as melhores funcionalidades de sistemas de rastreamento de geração solar e controle de bateria em uma solução unificada.

## 🌟 Funcionalidades

### Gerenciamento de Geração Solar
- **Consultas de Dados Históricos**: Consulte dados de geração solar por data ou período
- **Análise Estatística**: Obtenha estatísticas abrangentes sobre o desempenho do seu sistema solar
- **Conjunto de Dados Simulado**: Inclui um ano completo de dados de amostra de geração solar para testes

### Gerenciamento de Bateria
- **Monitoramento de Status da Bateria**: Verifique o uso atual, status de carregamento e nível da bateria
- **Controle de Fluxo de Energia**: Monitore e controle para onde a energia da bateria está sendo direcionada
- **Gerenciamento Dinâmico de Destinos**: Adicione ou remova destinos de energia em tempo real

### Interface Dupla
- **API REST**: Serviço web completo baseado em FastAPI com documentação automática
- **Interface CLI**: Interface de chat interativa na linha de comando para interação direta

## 🚀 Início Rápido

### Pré-requisitos
- Python 3.9+
- Chave da API do Google Gemini

### Instalação

1. **Clone e instale as dependências:**
```bash
pip install -r requirements.txt
```

2. **Configure as variáveis de ambiente:**
Crie um arquivo `.env` na raiz do projeto:
```
GEMINI_API_KEY=sua_chave_api_gemini_aqui
```

### Uso

#### Modo API Web (Padrão)
```bash
python main.py
```
- Servidor inicia em `http://localhost:8001`
- Visite `http://localhost:8001/docs` para documentação interativa da API
- Visite `http://localhost:8001` para visão geral da API

#### Modo Chat CLI
```bash
python cli.py
```
- Interface de chat interativa no terminal
- Consultas em linguagem natural para dados solares e de bateria
- Digite `exit`, `quit`, ou `bye` para sair

#### Uso Direto do Módulo
```bash
python core/gemini.py
```
- Acesso direto à interface de chat
- Útil para testes e desenvolvimento

## 📡 Endpoints da API

### Interface Principal de Chat
- `POST /chat` - Interface de linguagem natural para todas as consultas
- `POST /command` - Endpoint legado (redireciona para /chat)

### Geração Solar
- `POST /solar/query` - Consultas diretas de geração solar
- `GET /solar/stats` - Estatísticas gerais de geração solar

### Gerenciamento de Bateria
- `GET /battery/status` - Status atual da bateria
- `GET /battery/energy-flow` - Informações de fluxo de energia da bateria
- `POST /battery/add-destinations` - Adicionar destinos de fluxo de energia
- `POST /battery/remove-destinations` - Remover destinos de fluxo de energia

### Sistema
- `GET /health` - Verificação de saúde e status do sistema
- `GET /` - Visão geral da API e lista de endpoints

## 💬 Exemplos de Consultas

### Linguagem Natural (Interface de Chat)
```
"Quanta energia eu gerei ontem?"
"Qual é o status da minha bateria?"
"Adicione TV e carregador do carro aos destinos da bateria"
"Mostre-me as estatísticas solares de janeiro de 2025"
"Remova as luzes da cozinha do fluxo da bateria"
```

### Chamadas Diretas da API
```bash
# Consulta de geração solar
curl -X POST "http://localhost:8001/solar/query" \
  -H "Content-Type: application/json" \
  -d '{"start_date": "2025-01-10", "end_date": "2025-01-15"}'

# Status da bateria
curl -X GET "http://localhost:8001/battery/status"

# Interface de chat
curl -X POST "http://localhost:8001/chat" \
  -H "Content-Type: application/json" \
  -d '{"user_input": "Quanta energia solar eu gerei esta semana?"}'
```

## 🏗️ Arquitetura

### Estrutura do Projeto
```
├── main.py                # Aplicação FastAPI principal
├── cli.py                 # Interface CLI para chat
├── core/
│   ├── gemini.py          # Integração aprimorada com Gemini AI
│   ├── solar_tools.py     # Funções de processamento de dados solares
│   └── battery.py         # Módulos de gerenciamento de bateria
├── api/
│   └── endpoints.py       # Definições dos endpoints da API
├── solar_generation.csv   # Conjunto de dados de amostra de geração solar
├── system_prompt.txt      # Configuração do prompt do sistema AI
├── requirements.txt       # Dependências Python
└── README.md             # Este arquivo
```

### Componentes Principais
- **Integração Gemini**: Usa o Gemini 2.0 Flash do Google com chamadas de função
- **Prompts do Sistema**: Comportamento configurável da IA através de arquivos de prompt externos
- **Declarações de Função**: Definições estruturadas de ferramentas para chamadas de função da IA
- **Integração Pandas**: Processamento eficiente de dados para consultas de geração solar
- **Framework FastAPI**: Framework web moderno e rápido com documentação automática

## 🔧 Configuração

### Prompt do Sistema
Edite `system_prompt.txt` para personalizar o comportamento e capacidades do assistente IA.

### Dados Solares
Substitua `solar_generation.csv` pelos seus dados reais de geração solar. O arquivo deve ter as colunas:
- `date`: Datas no formato ISO (YYYY-MM-DD)
- `energy_kwh`: Geração diária de energia em kWh

### Variáveis de Ambiente
- `GEMINI_API_KEY`: Sua chave da API do Google Gemini
- `GOOGLE_API_KEY`: Nome alternativo da variável de ambiente (para compatibilidade)

## 🧪 Testes

### Testes CLI
```bash
python cli.py
```
Teste várias consultas:
- Solar: "Quanta energia foi gerada em 2025-01-10?"
- Bateria: "Qual é o status atual da minha bateria?"
- Estatísticas: "Mostre-me as estatísticas gerais de geração solar"

### Testes da API
Use a documentação interativa em `http://localhost:8001/docs` ou teste com comandos curl.

## 📊 Formato dos Dados

### Dados de Geração Solar
```csv
date,energy_kwh
2025-01-01,26.99
2025-01-02,40.82
...
```

### Exemplos de Resposta da API
```json
// Resposta de consulta solar
{
  "kwh": 156.78,
  "period": "2025-01-10 to 2025-01-15"
}

// Resposta de estatísticas solares
{
  "total_kwh": 10234.56,
  "average_daily_kwh": 28.01,
  "max_daily_kwh": 41.69,
  "min_daily_kwh": 18.13,
  "total_days": 365
}
```

## 🤝 Contribuindo

Este projeto combina funcionalidades de múltiplos sistemas de gerenciamento solar e de bateria. Ao contribuir:

1. Mantenha compatibilidade com interfaces API e CLI
2. Siga os padrões estabelecidos de declaração de função para ferramentas IA
3. Atualize prompts do sistema ao adicionar novas capacidades
4. Inclua tratamento de erros e logging apropriados

## 📝 Licença

Este projeto integra múltiplos componentes para gerenciamento de geração solar e bateria. Certifique-se de estar em conformidade com todas as licenças relevantes.

## 🆘 Solução de Problemas

### Problemas Comuns

1. **Chave da API Não Encontrada**
   - Certifique-se de que o arquivo `.env` existe com `GEMINI_API_KEY`
   - Verifique se a chave da API é válida e tem as permissões necessárias

2. **Dados Solares Não Carregam**
   - Verifique se `solar_generation.csv` existe e tem o formato correto
   - Verifique a instalação do pandas: `pip install pandas`

3. **Chamadas de Função Não Funcionam**
   - Certifique-se de que todos os módulos core estão importados corretamente
   - Verifique se as declarações de função correspondem às assinaturas reais das funções

4. **Modo CLI Não Inicia**
   - Use o comando exato: `python cli.py`
   - Verifique se todas as dependências estão instaladas

### Modo Debug
Defina a variável de ambiente `DEBUG=true` para logging adicional e informações de erro.
