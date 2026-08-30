import { Landing } from "@/components/Landing";
import { OverviewClient } from "@/components/Overview";
import {
  getExceptions,
  getOverview,
  getQueue,
  getRuntimeSafety,
} from "@/lib/api";
import { currentSession } from "@/lib/session";

export const dynamic = "force-dynamic";

export default async function Page() {
  // Anonymous visitors get the landing page rather than a redirect to /login.
  // The dashboard below is still protected: it is only rendered for a verified
  // session, and every endpoint it reads rejects an unauthenticated caller.
  if (!(await currentSession())) {
    return <Landing />;
  }

  // The try wraps only the fetching. Constructing JSX inside a try/catch swallows
  // render errors from the component tree as if they were fetch failures, which is how
  // a broken child silently becomes "cannot reach the backend".
  let data: Awaited<ReturnType<typeof loadAll>>;
  try {
    data = await loadAll();
  } catch {
    return <BackendUnreachable />;
  }

  const [overview, queue, exceptions, runtime] = data;
  return (
    <OverviewClient
      initialOverview={overview}
      initialQueue={queue}
      initialExceptions={exceptions}
      emailMode={runtime.email}
    />
  );
}

function loadAll() {
  return Promise.all([
    getOverview(),
    getQueue(),
    getExceptions(),
    getRuntimeSafety(),
  ]);
}

function BackendUnreachable() {
  return (
    <div className="rounded-xl border border-rose-300 bg-rose-50 px-5 py-4 text-sm text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
      <p className="font-medium">Cannot reach the backend.</p>
      <p className="mt-1">
        Start it with{" "}
        <code className="font-mono text-rose-900 dark:text-rose-100">
          uv run uvicorn app.main:app --reload
        </code>
        , check that Postgres is running, or set{" "}
        <code className="font-mono text-rose-900 dark:text-rose-100">
          NEXT_PUBLIC_API_URL
        </code>
        .
      </p>
    </div>
  );
}
