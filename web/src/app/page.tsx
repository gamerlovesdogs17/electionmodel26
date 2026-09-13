import { ForecastDashboard } from "@/components/forecast-dashboard";

export default function Home() {
  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 sm:py-12">
      <ForecastDashboard />
      <footer className="mt-14 border-t border-[var(--line)] pt-6 text-xs leading-relaxed text-[var(--muted)]">
        Probabilistic Senate forecast — not betting advice. Independents without a
        Democratic nominee still count toward Democratic seat totals. Display
        ratings follow model P(Dem). Chamber control is ≥51 Dem seats; 50–50 is
        Republican via the Vice President. House is out of scope. Sources and
        limitations: model card / acceptance gates.
      </footer>
    </main>
  );
}
