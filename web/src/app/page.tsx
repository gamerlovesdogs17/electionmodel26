import { ForecastDashboard } from "@/components/forecast-dashboard";

export default function Home() {
  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 sm:py-12">
      <ForecastDashboard />
      <footer className="mt-14 border-t border-[var(--line)] pt-6 text-xs leading-relaxed text-[var(--muted)]">
        Research system only — not a public live forecast. Synthetic fixtures
        power the offline demo; replace with redistributable poll/results
        archives via the evidence warehouse. House, Electoral College, ratings,
        and markets are out of scope for this pass.
      </footer>
    </main>
  );
}
