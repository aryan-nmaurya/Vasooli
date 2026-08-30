export default function Loading() {
  return (
    <div className="space-y-7" aria-busy="true" aria-label="Loading dashboard">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-2">
          <div className="h-5 w-44 animate-pulse rounded bg-panel-2" />
          <div className="h-4 w-80 max-w-[70vw] animate-pulse rounded bg-panel-2" />
        </div>
        <div className="h-9 w-36 animate-pulse rounded-md bg-panel-2" />
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((item) => (
          <div key={item} className="h-24 animate-pulse rounded-xl border border-line bg-panel" />
        ))}
      </div>
      <div className="h-72 animate-pulse rounded-xl border border-line bg-panel" />
    </div>
  );
}
