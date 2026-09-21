"use client";

import { Bell, BellOff, BookMarked, Check, Copy, Download, FileText, Link2, Plus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import type { AnswerModel } from "@/components/answer/answer-model";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAddToBinder, useBinders, useCreateBinder, useFollow, useShare, useVersions } from "@/hooks/use-api";
import { downloadText, exportBibtex, exportRis, formatReferenceList } from "@/lib/citations";
import { stripCitations } from "@/lib/text";

/** Share · Follow · Save to binder · Export — on every answer. */
export function AnswerActions({ answer }: { answer: AnswerModel }) {
  if (!answer.answerId) return null;
  return (
    <>
      <ShareButton answerId={answer.answerId} />
      <FollowButton answerId={answer.answerId} />
      <SaveToBinder answerId={answer.answerId} />
      <ExportMenu answer={answer} />
    </>
  );
}

function ShareButton({ answerId }: { answerId: string }) {
  const share = useShare();
  const [copied, setCopied] = useState(false);
  const onShare = () =>
    share.mutate(answerId, {
      onSuccess: async (link) => {
        const url = `${window.location.origin}${link.path}`;
        try {
          await navigator.clipboard.writeText(url);
          setCopied(true);
          toast.success("Public link copied", { description: url });
          window.setTimeout(() => setCopied(false), 2000);
        } catch {
          toast.message("Public link", { description: url });
        }
      },
      onError: (e) => toast.error(e instanceof Error ? e.message : "Could not create a link."),
    });
  return (
    <Tooltip>
      <TooltipTrigger render={<Button variant="ghost" size="icon-sm" aria-label="Copy public link" onClick={onShare} />}>
        {copied ? <Check className="size-4" /> : <Link2 className="size-4" />}
      </TooltipTrigger>
      <TooltipContent>Copy a read-only public link</TooltipContent>
    </Tooltip>
  );
}

function FollowButton({ answerId }: { answerId: string }) {
  const versions = useVersions(answerId);
  const follow = useFollow(answerId);
  const following = versions.data?.following ?? false;
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            aria-pressed={following}
            aria-label={following ? "Stop following this answer" : "Follow this answer for updates"}
            disabled={follow.isPending}
            onClick={() =>
              follow.mutate(!following, {
                onSuccess: () =>
                  toast.success(following ? "Unfollowed." : "Following — you'll be notified if the evidence changes."),
              })
            }
          />
        }
      >
        {following ? <Bell className="size-4 fill-current" /> : <BellOff className="size-4" />}
      </TooltipTrigger>
      <TooltipContent>{following ? "Following (Living Answer)" : "Follow for updates"}</TooltipContent>
    </Tooltip>
  );
}

export function SaveToBinder({
  answerId,
  chunkId,
  open: controlledOpen,
  onOpenChange,
}: {
  answerId?: string;
  chunkId?: string;
  /** Controlled mode: the caller owns the dialog and renders its own trigger. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  const controlled = controlledOpen !== undefined;
  const open = controlled ? controlledOpen : uncontrolledOpen;
  const setOpen = (next: boolean) => (controlled ? onOpenChange?.(next) : setUncontrolledOpen(next));
  const [newTitle, setNewTitle] = useState("");
  const binders = useBinders();
  const create = useCreateBinder();
  const add = useAddToBinder();

  const save = (binderId: string) =>
    add.mutate(
      {
        binderId,
        body: chunkId
          ? { item_type: "passage", chunk_id: chunkId, answer_id: null }
          : { item_type: "answer", answer_id: answerId ?? null, chunk_id: null },
      },
      {
        onSuccess: () => {
          setOpen(false);
          toast.success("Saved to binder.");
        },
        onError: (e) => toast.error(e instanceof Error ? e.message : "Could not save."),
      },
    );

  const createAndSave = () =>
    create.mutate(
      { title: newTitle.trim(), visibility: "private", description: null },
      { onSuccess: (b) => save(b.id) },
    );

  return (
    <>
      {!controlled && (
        <Tooltip>
          <TooltipTrigger render={<Button variant="ghost" size="icon-sm" aria-label="Save to binder" onClick={() => setOpen(true)} />}>
            <BookMarked className="size-4" />
          </TooltipTrigger>
          <TooltipContent>Save to an Evidence Binder</TooltipContent>
        </Tooltip>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Save to binder</DialogTitle>
            <DialogDescription>
              {chunkId ? "Add this passage" : "Add this answer"} to a collection you can annotate and present.
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-64 space-y-1 overflow-y-auto">
            {binders.data?.binders.map((b) => (
              <button
                key={b.id}
                type="button"
                onClick={() => save(b.id)}
                disabled={add.isPending}
                className="flex w-full items-center justify-between rounded-sm px-2 py-2 text-left text-sm hover:bg-accent focus-visible:bg-accent"
              >
                <span className="truncate">{b.title}</span>
                <span className="text-xs text-muted-foreground">
                  {b.item_count} item{b.item_count === 1 ? "" : "s"} · {b.visibility}
                </span>
              </button>
            ))}
            {binders.data && binders.data.binders.length === 0 && (
              <p className="px-2 py-3 text-sm text-muted-foreground">No binders yet — create the first one below.</p>
            )}
          </div>
          <DialogFooter className="gap-2 sm:justify-between">
            <Input
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="New binder title"
              aria-label="New binder title"
              onKeyDown={(e) => e.key === "Enter" && newTitle.trim() && createAndSave()}
            />
            <Button size="sm" disabled={!newTitle.trim() || create.isPending} onClick={createAndSave}>
              <Plus /> Create &amp; save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function ExportMenu({ answer }: { answer: AnswerModel }) {
  const copy = async (label: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`${label} copied to clipboard`);
    } catch {
      toast.error("Clipboard unavailable — try the download options.");
    }
  };
  const slug = stripCitations(answer.query).slice(0, 40).replace(/[^A-Za-z0-9]+/g, "-").toLowerCase() || "answer";
  const none = answer.citations.length === 0;
  return (
    <DropdownMenu>
      <Tooltip>
        <TooltipTrigger render={<DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="Export" />} />}>
          <Download className="size-4" />
        </TooltipTrigger>
        <TooltipContent>Export</TooltipContent>
      </Tooltip>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuGroup>
        <DropdownMenuLabel>Copy references</DropdownMenuLabel>
        <DropdownMenuItem disabled={none} onClick={() => void copy("Vancouver references", formatReferenceList(answer.citations, "vancouver"))}>
          <Copy /> Vancouver
        </DropdownMenuItem>
        <DropdownMenuItem disabled={none} onClick={() => void copy("AMA references", formatReferenceList(answer.citations, "ama"))}>
          <Copy /> AMA
        </DropdownMenuItem>
        <DropdownMenuItem
          onClick={() =>
            void copy(
              "Answer",
              `${answer.query}\n\n${answer.content}\n\nReferences\n${formatReferenceList(answer.citations, "vancouver")}`,
            )
          }
        >
          <FileText /> Answer with references (text)
        </DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
        <DropdownMenuLabel>Download</DropdownMenuLabel>
        <DropdownMenuItem disabled={none} onClick={() => downloadText(`${slug}.bib`, exportBibtex(answer.citations), "application/x-bibtex")}>
          <Download /> BibTeX (.bib)
        </DropdownMenuItem>
        <DropdownMenuItem disabled={none} onClick={() => downloadText(`${slug}.ris`, exportRis(answer.citations), "application/x-research-info-systems")}>
          <Download /> RIS (.ris)
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => void import("@/components/answer/export-pdf").then((m) => m.exportAnswerPdf(answer, slug))}>
          <FileText /> PDF with citations
        </DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
