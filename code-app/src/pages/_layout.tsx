import { Outlet } from "react-router-dom";
import { FileText } from "lucide-react";

export default function Layout() {
  return (
    <div className="min-h-dvh flex flex-col bg-[#fafaff]">
      <header className="h-10 border-b border-[#e5e2f0] bg-white flex items-center px-4 shrink-0">
        <div className="flex items-center gap-2">
          <div className="h-6 w-6 rounded-md bg-[#6B4EE6] flex items-center justify-center">
            <FileText className="h-3.5 w-3.5 text-white" />
          </div>
          <span className="font-semibold text-sm text-[#1a1a2e]">
            Financial Statement Review
          </span>
        </div>
      </header>
      <main className="flex-1 overflow-hidden flex flex-col">
        <Outlet />
      </main>
    </div>
  );
}
