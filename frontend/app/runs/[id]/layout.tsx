import { RequireAuth } from "@/components/RequireAuth";
import { RunShell } from "@/components/RunShell";

export default async function RunDetailLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <RequireAuth>
      <RunShell runId={id}>{children}</RunShell>
    </RequireAuth>
  );
}
