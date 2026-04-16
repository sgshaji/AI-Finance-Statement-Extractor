# Finance Statement Agent

AI-powered financial statement extraction platform built on Azure. Upload a PDF financial report, and the agent extracts Balance Sheet, Income Statement, and Cash Flow data into structured JSON and formatted Excel.

Supports multi-language documents (English, Chinese, French, Japanese) with automatic label translation.

## Architecture

```
User uploads PDF via Copilot Studio
       |
       v
+------------------+     +-------------------------+     +---------------------+
| Copilot Studio   | --> | Custom Connector        | --> | Azure Function      |
| Agent (GPT-4.1)  |     | (REST API, API key auth)|     | (Python 3.11)       |
|                  |     +-------------------------+     |                     |
| - File detection |                                     | 5-Stage Pipeline:   |
| - Polling loop   |                                     |  1. Analyze (DI)    |
| - Summary card   |                                     |  2. Select           |
| - Excel download |                                     |  3. Extract          |
+------------------+                                     |  4. Enrich (GPT-4.1)|
                                                         |  5. Validate         |
                                                         +---------------------+
                                                                  |
                                                                  v
                                                         Blob Storage (results)
                                                                  |
                                                                  v
                                                         Excel Generation
```

## Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| **Python** | 3.11+ | Azure Functions runtime |
| **Node.js** | 18+ | Code App frontend build |
| **Azure CLI** | 2.50+ | Azure resource management and auth |
| **Azure Functions Core Tools** | 4.x | Local function development and deployment |
| **VS Code** | Latest | Recommended IDE with Copilot Studio extension |
| **Git** | 2.x | Version control |

### Azure Subscription

You need an Azure subscription with permissions to create:
- Resource Group
- Azure Function App (Consumption or Premium plan)
- Azure Document Intelligence (S0 tier)
- Azure OpenAI (with GPT-4.1 deployment)
- Storage Account (for blob storage)

### Power Platform (for Copilot Studio agent)

- Copilot Studio license
- Power Platform environment with Dataverse
- Custom connector permissions

## Installation

### 1. Clone the Repository

```bash
git clone <repo-url>
cd finance-statement-agent
```

### 2. Azure Login

```bash
az login
az account set --subscription "<your-subscription-id>"
```

### 3. Set Up Azure Resources

If resources are not yet provisioned:

```bash
# Create resource group
az group create --name rg-finance-agent --location swedencentral

# Create Document Intelligence
az cognitiveservices account create \
  --name fin-stmt-docai \
  --resource-group rg-finance-agent \
  --location swedencentral \
  --kind FormRecognizer --sku S0

# Create Azure OpenAI + deploy GPT-4.1
az cognitiveservices account create \
  --name fin-stmt-oai \
  --resource-group rg-finance-agent \
  --location swedencentral \
  --kind OpenAI --sku S0

az cognitiveservices account deployment create \
  --name fin-stmt-oai \
  --resource-group rg-finance-agent \
  --deployment-name gpt-4.1 \
  --model-name gpt-4.1 \
  --model-version "2025-04-14" \
  --model-format OpenAI \
  --sku-name Standard --sku-capacity 10

# Create Function App
az storage account create \
  --name finstmtfuncstor \
  --resource-group rg-finance-agent \
  --location swedencentral --sku Standard_LRS

az functionapp create \
  --name fin-stmt-extractor-v2 \
  --resource-group rg-finance-agent \
  --storage-account finstmtfuncstor \
  --consumption-plan-location swedencentral \
  --runtime python --runtime-version 3.11 \
  --functions-version 4 --os-type Linux
```

### 4. Configure the Backend

```bash
cd azure-functions
cp .env.example .env
```

Edit `.env` with your resource endpoints:

```
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=https://fin-stmt-docai.cognitiveservices.azure.com/
AZURE_OPENAI_ENDPOINT=https://fin-stmt-oai.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-4.1
AZURE_OPENAI_API_VERSION=2024-12-01-preview
EXTRACTION_BACKEND=document_intelligence
```

### 5. Install Dependencies

```bash
cd azure-functions
pip install -r requirements.txt
```

### 6. Run Locally

```bash
cd azure-functions
func start
# API available at http://localhost:7071/api/health
```

### 7. Run Tests

```bash
cd azure-functions
pytest
```

### 8. Deploy to Azure

```bash
cd azure-functions
func azure functionapp publish fin-stmt-extractor-v2 --python
```

### 9. Set Up Custom Connector (Copilot Studio)

1. Import `docs/custom-connector-swagger.yml` into Power Platform custom connectors
2. Set the API key (get it from `az functionapp keys list --name fin-stmt-extractor-v2 --resource-group rg-finance-agent`)
3. Create a connection using the new connector
4. Push the CPS agent YAML via the VS Code extension

## Project Structure

```
finance-statement-agent/
|
+-- azure-functions/                  # Backend — Python Azure Function App
|   +-- function_app.py               # Thin HTTP router (~240 lines)
|   +-- extractor/
|   |   +-- job_store.py              # Blob storage CRUD, response builder
|   |   +-- extract_endpoints.py      # /extract, /extract-by-url, /status, /fx-rate
|   |   +-- excel_endpoint.py         # /generate-excel (build, upload, SAS URL)
|   |   +-- review_endpoints.py       # /build-review-card, /parse-card-submission
|   |   +-- pipeline.py               # 5-stage orchestrator
|   |   +-- stages/
|   |   |   +-- contracts.py          # Shared types (PipelineOptions, AnalyzeResult)
|   |   |   +-- analyze.py            # Stage 1: PDF -> markdown (backend toggle)
|   |   |   +-- select.py             # Stage 2: Score & pick best candidate
|   |   |   +-- extract.py            # Stage 3: Heading search + HTML table parsing
|   |   |   +-- enrich.py             # Stage 4: LLM translation, canonical keys
|   |   |   +-- validate.py           # Stage 5: Quality scoring (0.0-1.0)
|   |   +-- di_client.py              # Azure Document Intelligence (Managed Identity)
|   |   +-- di_adapter.py             # DI -> page_map + LLM classification
|   |   +-- cu_client.py              # Content Understanding client (legacy)
|   |   +-- textract_client.py        # AWS Textract client (alternative)
|   |   +-- pdfplumber_client.py      # Local PDF extraction (no cloud)
|   |   +-- html_table_parser.py      # HTML <table> -> rows/columns/cells
|   |   +-- llm_reconciler.py         # LLM post-processing for label quality
|   |   +-- schema_mapper.py          # Row enrichment + v1.2 JSON assembly
|   |   +-- excel_formatter.py        # Professional Excel workbook builder
|   |   +-- card_builder.py           # Adaptive Card JSON for CPS display
|   |   +-- confidence_scorer.py      # Statement quality scoring
|   +-- tests/                        # pytest unit + integration tests
|   +-- requirements.txt
|   +-- host.json
|   +-- .env.example                  # Environment variable template
|
+-- copilot-studio-agent/             # CPS Agent — Copilot Studio YAML
|   +-- agent.mcs.yml                 # Agent definition (instructions, model)
|   +-- topics/
|   |   +-- DocumentProcessorV2.mcs.yml  # File upload -> extract -> poll -> summary
|   |   +-- HITLReview_wX5.mcs.yml       # Review navigator + Excel generation
|   |   +-- GenerateExcel.mcs.yml        # Standalone Excel generation
|   +-- actions/                      # Connector action wrappers (TaskDialog YAML)
|   +-- variables/                    # Global variables (jobId, summary, etc.)
|   +-- workflows/                    # Power Automate flows
|
+-- code-app/                         # Power Apps Code Component (React + TypeScript)
|   +-- src/
|   |   +-- pages/review.tsx          # HITL review grid page
|   |   +-- services/dataverse-service.ts  # Dataverse CRUD operations
|   |   +-- components/review/        # Table, tabs, summary bar, editable cells
|
+-- docs/
|   +-- ARCHITECTURE-FINAL.md         # Detailed architecture documentation
|   +-- DEMO-SCRIPT.md                # Demo walkthrough script
|   +-- custom-connector-swagger.yml  # Swagger for the custom connector (v5.0.0)
|
+-- .github/workflows/
    +-- deploy-function.yml            # CI/CD: deploy to Azure on push to main
```

## Extraction Backends

The pipeline supports 4 backends, toggled via the `EXTRACTION_BACKEND` environment variable:

| Backend | Env Value | Service | Auth | Best For |
|---|---|---|---|---|
| **Document Intelligence** | `document_intelligence` | Azure DI prebuilt-layout | Managed Identity | Production (current default) |
| **Content Understanding** | `cu` | Azure CU custom analyzers | Managed Identity | Legacy |
| **Textract** | `textract` | AWS Textract | IAM credentials | AWS-native customers |
| **pdfplumber** | `pdfplumber` | Local Python | None | Testing, no cloud cost |

All backends produce the same output: markdown with HTML `<table>` blocks, which feeds into the shared Stages 2-5.

## Azure Resources (Sweden Central)

| Resource | Name | Purpose |
|---|---|---|
| Resource Group | `rg-finance-agent` | Container |
| Document Intelligence | `fin-stmt-docai` | PDF table extraction (prebuilt-layout) |
| Azure OpenAI | `fin-stmt-oai` (GPT-4.1) | Classification, translation, enrichment |
| Function App | `fin-stmt-extractor-v2` | API endpoints (Consumption plan, Linux) |
| Storage Account | `finstmtfuncstor` | Blob storage for job results + Excel files |

Authentication: **Managed Identity only** (no API keys). The Function App's system-assigned identity has `Cognitive Services User` and `Cognitive Services OpenAI User` roles at the resource group scope.

## API Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/extract` | Function key | Submit PDF for extraction (returns jobId) |
| `POST` | `/api/extract-by-url` | Function key | Submit PDF URL for extraction |
| `GET` | `/api/extract/status/{jobId}` | Anonymous | Poll job status and results |
| `POST` | `/api/generate-excel` | Function key | Generate Excel from extraction result |
| `POST` | `/api/build-review-card` | Function key | Build Adaptive Card for CPS display |
| `POST` | `/api/parse-card-submission` | Function key | Parse Adaptive Card submit payload |
| `POST` | `/api/apply-corrections` | Function key | Apply analyst corrections |
| `GET` | `/api/fx-rate` | Anonymous | Fetch FX conversion rates |
| `GET` | `/api/health` | Function key | Health check |

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` | Yes (if DI backend) | DI resource endpoint URL |
| `AZURE_OPENAI_ENDPOINT` | Yes | Azure OpenAI resource endpoint URL |
| `AZURE_OPENAI_DEPLOYMENT` | Yes | Model deployment name (e.g., `gpt-4.1`) |
| `AZURE_OPENAI_API_VERSION` | Yes | API version (e.g., `2024-12-01-preview`) |
| `EXTRACTION_BACKEND` | No | Backend toggle: `document_intelligence` (default), `cu`, `textract`, `pdfplumber` |
| `AZURE_CU_ENDPOINT` | Only if CU backend | Content Understanding endpoint URL |
| `AWS_REGION` | Only if Textract backend | AWS region for Textract |
| `AWS_S3_BUCKET` | Only if Textract backend | S3 bucket for temp PDF upload |

All Azure services use Managed Identity authentication. No API key environment variables are needed.
