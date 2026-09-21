"use client";

import { BookMarked, Plus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { EmptyState, ErrorState, ListSkeleton, PageBody, PageHeader } from "@/components/clinical/page";
import { useAuth } from "@/components/providers/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useBinders, useCreateBinder } from "@/hooks/use-api";
import { formatDate } from "@/lib/text";

export default function BindersPage() {
  const binders = useBinders();
  const { role } = useAuth();
  const canCurate = role !== "viewer";

  return (
    <PageBody>
      <PageHeader
        title="Evidence Binders"
        description="Collections of answers and passages — annotated, shareable with your organisation, ready to present."
        actions={canCurate && <NewBinder />}
      />
      {binders.isPending ? (
        <ListSkeleton rows={4} />
      ) : binders.isError ? (
        <ErrorState error={binders.error} onRetry={() => void binders.refetch()} />
      ) : binders.data.binders.length === 0 ? (
        <EmptyState
          icon={BookMarked}
          title="No binders yet"
          description="Save any answer or passage from the bookmark icon, or create a binder here for an upcoming journal club."
          action={canCurate ? <NewBinder /> : undefined}
        />
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-label="Binders">
          {binders.data.binders.map((b) => (
            <li key={b.id}>
              <Link
                href={`/app/binders/${b.id}`}
                className="flex h-full flex-col gap-2 rounded-lg border bg-card p-4 hover:bg-accent/50 focus-visible:bg-accent/50"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="line-clamp-2 text-sm font-medium">{b.title}</span>
                  <Badge variant={b.visibility === "org" ? "secondary" : "outline"}>{b.visibility === "org" ? "Organisation" : "Private"}</Badge>
                </div>
                {b.description && <p className="line-clamp-2 text-xs text-muted-foreground">{b.description}</p>}
                <p className="mt-auto font-mono text-[11px] text-muted-foreground">
                  {b.item_count} item{b.item_count === 1 ? "" : "s"} · {b.created_by_name ?? "—"} · {formatDate(b.created_at)}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </PageBody>
  );
}

function NewBinder() {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [shared, setShared] = useState(false);
  const create = useCreateBinder();
  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>
        <Plus /> New binder
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>New binder</DialogTitle>
            <DialogDescription>A collection to gather evidence around one question or session.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="binder-title">Title</Label>
              <Input id="binder-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Journal club — DOACs in AF" autoFocus />
            </div>
            <div className="space-y-1">
              <Label htmlFor="binder-desc">Description</Label>
              <Textarea id="binder-desc" value={description} onChange={(e) => setDescription(e.target.value)} rows={2} placeholder="Optional" />
            </div>
            <label className="flex items-center justify-between gap-3 text-sm">
              <span>
                Share with the organisation
                <span className="block text-xs text-muted-foreground">Otherwise only you can see it.</span>
              </span>
              <Switch checked={shared} onCheckedChange={setShared} aria-label="Share with organisation" />
            </label>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button
              disabled={!title.trim() || create.isPending}
              onClick={() =>
                create.mutate(
                  { title: title.trim(), description: description.trim() || null, visibility: shared ? "org" : "private" },
                  {
                    onSuccess: () => {
                      setOpen(false);
                      setTitle("");
                      setDescription("");
                      toast.success("Binder created.");
                    },
                    onError: (e) => toast.error(e instanceof Error ? e.message : "Could not create the binder."),
                  },
                )
              }
            >
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
