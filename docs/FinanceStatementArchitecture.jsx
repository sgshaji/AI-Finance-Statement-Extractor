import React from "react";
import { ArrowRight } from "lucide-react";

// Local logo paths (relative to docs/)
const logos = {
  user: "./icons/user.png",
  teams: "./icons/teams.png",
  copilotStudio: "./icons/copilot-studio.jpeg",
  topic: "./icons/flow-chart.png",
  connector: "./icons/api-settings.png",
  azureFunctions: "./icons/azure-functions.png",
  contentUnderstanding: "./icons/azure-functions.png",
  azureOpenAI: "./icons/azure-openai.png",
  tableParser: "./icons/parse-clipboard.png",
  schemaMapper: "./icons/json.png",
  blobStorage: "./icons/azure-blob-storage.png",
  powerAutomate: "./icons/power-automate.jpeg",
  excel: "./icons/excel.png",
  sharePoint: "./icons/sharepoint.png",
  dataverse: "./icons/dataverse.jpeg",
  powerApps: "./icons/power-apps.png",
  checkmark: "./icons/checkmark.png",
};

const Card = ({ logo, title, subtitle, accent = "blue", large = false }) => {
  const accentMap = {
    green: "border-emerald-500/20 bg-emerald-500/5",
    blue: "border-sky-500/20 bg-sky-500/5",
    purple: "border-violet-500/20 bg-violet-500/5",
    orange: "border-amber-500/20 bg-amber-500/5",
    pink: "border-pink-500/20 bg-pink-500/5",
  };

  return (
    <div
      className={`rounded-2xl border border-white/10 bg-white/5 backdrop-blur-sm shadow-[0_0_0_1px_rgba(255,255,255,0.02),0_10px_30px_rgba(0,0,0,0.35)] ${large ? "p-6 min-h-[118px]" : "p-5 min-h-[108px]"}`}
    >
      <div className="flex items-start gap-4">
        <div className={`mt-0.5 rounded-xl border p-2.5 ${accentMap[accent]}`}>
          <img
            src={logo}
            alt={title}
            className="h-8 w-8 shrink-0 object-contain"
          />
        </div>
        <div className="min-w-0">
          <div className="text-xl font-semibold tracking-tight text-white leading-tight">{title}</div>
          <div className="mt-1 text-sm md:text-base text-slate-400 leading-snug">{subtitle}</div>
        </div>
      </div>
    </div>
  );
};

const Section = ({ title, color, children, className = "" }) => {
  const styles = {
    green: "border-emerald-500/70 shadow-[0_0_0_1px_rgba(16,185,129,0.15)]",
    blue: "border-sky-500/70 shadow-[0_0_0_1px_rgba(14,165,233,0.15)]",
    purple: "border-violet-500/70 shadow-[0_0_0_1px_rgba(139,92,246,0.15)]",
    orange: "border-amber-500/70 shadow-[0_0_0_1px_rgba(245,158,11,0.15)]",
    pink: "border-pink-500/70 shadow-[0_0_0_1px_rgba(236,72,153,0.15)]",
  };

  const label = {
    green: "text-emerald-400",
    blue: "text-sky-400",
    purple: "text-violet-400",
    orange: "text-amber-400",
    pink: "text-pink-400",
  };

  return (
    <section className={`relative rounded-[28px] border bg-slate-950/40 p-5 md:p-6 ${styles[color]} ${className}`}>
      <div className={`absolute -top-3 left-6 bg-[#08111f] px-3 text-sm font-bold uppercase tracking-[0.18em] ${label[color]}`}>
        {title}
      </div>
      {children}
    </section>
  );
};

const Arrow = () => <ArrowRight className="h-6 w-6 text-slate-500 shrink-0" />;

export default function FinanceStatementArchitectureWebpage() {
  return (
    <div className="min-h-screen w-full bg-[#08111f] text-white">
      <div className="mx-auto max-w-[1700px] px-6 py-10 md:px-10 md:py-12">
        <header className="mb-10 text-center">
          <h1 className="text-3xl md:text-5xl font-bold tracking-tight text-white">
            Finance Statement Analysis Agent
          </h1>
          <p className="mt-3 text-lg md:text-xl text-slate-400">Solution Architecture</p>
        </header>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[320px_minmax(0,1fr)_310px]">
          {/* Client */}
          <Section title="Client" color="green" className="h-full">
            <div className="grid gap-4">
              <Card
                logo={logos.user}
                title="End User"
                subtitle="Uploads PDF financial report"
                accent="green"
                large
              />
              <Card
                logo={logos.teams}
                title="Chat Interface"
                subtitle="Teams / Web / Custom"
                accent="green"
                large
              />
            </div>
          </Section>

          {/* Middle column */}
          <div className="grid gap-6">
            <Section title="Microsoft Copilot Studio" color="blue">
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                <Card
                  logo={logos.copilotStudio}
                  title="Finance Statement Agent"
                  subtitle="GPT-4.1 orchestrator"
                  accent="blue"
                />
                <Card
                  logo={logos.topic}
                  title="Document Processor Topic"
                  subtitle="Submit, poll, generate flow"
                  accent="blue"
                />
                <Card
                  logo={logos.connector}
                  title="Custom Connector"
                  subtitle="Swagger 2.0 · 2 operations"
                  accent="blue"
                />
              </div>
            </Section>

            <Section title="Microsoft Azure" color="purple">
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                <Card
                  logo={logos.azureFunctions}
                  title="Azure Functions"
                  subtitle="Python · Async HTTP"
                  accent="purple"
                />
                <Card
                  logo={logos.contentUnderstanding}
                  title="Content Understanding"
                  subtitle="Locator + Extractor models"
                  accent="purple"
                />
                <Card
                  logo={logos.azureOpenAI}
                  title="Azure OpenAI"
                  subtitle="Label reconciliation"
                  accent="purple"
                />
                <Card
                  logo={logos.tableParser}
                  title="Table Parser"
                  subtitle="HTML → structured rows"
                  accent="purple"
                />
                <Card
                  logo={logos.schemaMapper}
                  title="Schema Mapper"
                  subtitle="v1.2 JSON · validation"
                  accent="purple"
                />
                <Card
                  logo={logos.blobStorage}
                  title="Blob Storage"
                  subtitle="Async job state"
                  accent="purple"
                />
              </div>
            </Section>
          </div>

          {/* Microsoft 365 */}
          <Section title="Microsoft 365" color="orange" className="h-full">
            <div className="grid gap-4">
              <Card
                logo={logos.powerAutomate}
                title="Power Automate"
                subtitle="Excel generation flow"
                accent="orange"
                large
              />
              <Card
                logo={logos.excel}
                title="Office Scripts"
                subtitle="4 formatted worksheets"
                accent="orange"
                large
              />
              <Card
                logo={logos.sharePoint}
                title="SharePoint Online"
                subtitle="File storage & sharing"
                accent="orange"
                large
              />
            </div>
          </Section>
        </div>

        {/* Human-in-the-Loop Review */}
        <Section title="Human-in-the-Loop Review" color="pink" className="mt-6">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div className="grid flex-1 grid-cols-1 gap-4 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_auto_minmax(0,1fr)_auto_minmax(0,1fr)] md:items-center">
              <Card
                logo={logos.dataverse}
                title="Dataverse"
                subtitle="Extraction results stored"
                accent="pink"
              />
              <div className="hidden md:flex justify-center"><Arrow /></div>
              <Card
                logo={logos.powerApps}
                title="Power Apps"
                subtitle="Review & correction interface"
                accent="blue"
              />
              <div className="hidden md:flex justify-center"><Arrow /></div>
              <Card
                logo={logos.checkmark}
                title="Human Reviewer"
                subtitle="Verify, correct, approve"
                accent="green"
              />
              <div className="hidden md:flex justify-center"><Arrow /></div>
              <Card
                logo={logos.excel}
                title="Excel Export"
                subtitle="Approved data → workbook"
                accent="green"
              />
            </div>
          </div>
        </Section>

        <footer className="mt-10 border-t border-white/10 pt-6 text-center text-sm md:text-base text-slate-500 leading-relaxed">
          Data flow: User → Copilot Studio → Custom Connector → Azure Functions (async) → Copilot Studio → Power Automate → Excel → SharePoint · Async polling handles 30s connector timeout · Multi-language (CN, JP, EN)
        </footer>
      </div>
    </div>
  );
}
