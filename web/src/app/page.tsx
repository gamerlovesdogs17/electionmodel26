import { ForecastDashboard } from "@/components/forecast-dashboard";

export default function Home() {
  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 sm:py-12">
      <ForecastDashboard />
      <footer className="mt-14 border-t border-[var(--line)] pt-6 text-xs leading-relaxed text-[var(--muted)]">
        Research system only — not a public live forecast. 50–50 chambers count as
        Republican control (VP tiebreak). ALFRED vintages / OpenFEC shares use live
        APIs when keyed, otherwise fixtures. Ratings and Kalshi markets are on by
        default (disable with `--no-ratings` / `--no-markets`). House is out of
        scope.
      </footer>
    </main>
  );
}
