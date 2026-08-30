/**
 * Authenticated file upload proxy, for ledger imports.
 *
 * Separate from /api/action because that route reads and re-serialises JSON, which
 * would destroy a multipart body. This forwards the raw stream and returns the
 * backend's JSON verdict unchanged.
 *
 * The upload itself is only ever a parse-and-report on the first call: the backend
 * defaults to a dry run and writes nothing unless `dry_run=false` is explicitly sent.
 */

import { NextResponse } from "next/server";

import { currentSessionToken } from "@/lib/session";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

//: Matches the backend's own cap, so an oversized file is refused here rather than
//: streamed across the network first.
const MAX_BYTES = 5 * 1024 * 1024;

export async function POST(request: Request) {
  const sessionToken = await currentSessionToken();
  if (!sessionToken) {
    return NextResponse.json({ error: "Not signed in" }, { status: 401 });
  }

  const form = await request.formData();
  const file = form.get("file");
  if (!(file instanceof File)) {
    return NextResponse.json({ error: "No file provided" }, { status: 400 });
  }
  if (file.size > MAX_BYTES) {
    return NextResponse.json({ error: "File is larger than 5 MB." }, { status: 413 });
  }

  // Rebuilt rather than forwarded wholesale: only the fields the import endpoint
  // accepts travel onward, and `dry_run` is normalised so a missing value can never
  // be read as "go ahead and write".
  const outgoing = new FormData();
  outgoing.set("file", file, file.name);
  outgoing.set("dry_run", form.get("dry_run") === "false" ? "false" : "true");
  outgoing.set("rebase_dates", form.get("rebase_dates") === "true" ? "true" : "false");

  const upstream = await fetch(`${API_BASE}/api/invoices/import`, {
    method: "POST",
    headers: { Cookie: `vasooli_session=${sessionToken}` },
    body: outgoing,
    cache: "no-store",
  });

  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}
