"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, FileUp, Loader2, Trash2, UploadCloud } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, type DragEvent } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/providers/auth-provider";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useToken } from "@/hooks/use-api";
import { ApiError } from "@/lib/api";
import { api } from "@/lib/api-client";
import type { Schemas } from "@/lib/domain";
import { cn } from "@/lib/utils";

type Paper = Schemas["PaperOut"];

export const PAPERS_KEY = ["learn", "papers"] as const;
const MAX_BYTES = 25 * 1024 * 1024;

export function designLabel(studyType: string | null | undefined): string | null {
  if (!studyType) return null;
  const labels: Record<string, string> = {
    randomized_controlled_trial: "Randomised trial",
    meta_analysis: "Meta-analysis",
    systematic_review: "Systematic review",
    guideline: "Guideline",
    cohort: "Cohort study",
    case_control: "Case-control study",
    cross_sectional: "Cross-sectional study",
  };
  return labels[studyType] ?? studyType.replace(/_/g, " ");
}

function PaperCard({ paper, canDelete, onDelete }: { paper: Paper; canDelete: boolean; onDelete: (paper: Paper) => void }) {
  const design = designLabel(paper.study_type);
  return (
    <li className="flex items-start gap-3 rounded-xl border bg-card p-3 transition-colors hover:border-primary/40" data-testid="paper-card">
      <FileText className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden />
      <div className="min-w-0 flex-1">
        <Link href={`/app/learn/papers/${paper.id}`} className="font-medium leading-5 hover:underline">
          {paper.title}
        </Link>
        <p className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
          {design && <span className="rounded bg-muted px-1.5 font-medium text-foreground">{design}</span>}
          {paper.pages !== null && paper.pages !== undefined && (
            <span>
              {paper.pages} page{paper.pages === 1 ? "" : "s"}
            </span>
          )}
          <span>{new Date(paper.uploaded_at).toLocaleDateString()}</span>
          {paper.uploaded_by_you && <span>uploaded by you</span>}
        </p>
      </div>
      {canDelete && (
        <Button variant="ghost" size="icon-sm" onClick={() => onDelete(paper)} aria-label={`Delete ${paper.title}`} data-testid="paper-delete">
          <Trash2 />
        </Button>
      )}
    </li>
  );
}

/**
 * Ask-this-Paper's library: upload a research paper, and the workspace's
 * papers to open and question.
 */
export function PaperLibrary() {
  const token = useToken();
  const { role } = useAuth();
  const router = useRouter();
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [doomed, setDoomed] = useState<Paper | null>(null);

  const papers = useQuery({ queryKey: PAPERS_KEY, queryFn: () => api.papers(token), enabled: Boolean(token) });
  const upload = useMutation({
    mutationFn: (file: File) => api.uploadPaper(token, file),
    onSuccess: (paper) => {
      void queryClient.invalidateQueries({ queryKey: PAPERS_KEY });
      if (paper.deduplicated) toast.message("This paper is already in your workspace — opening it.");
      router.push(`/app/learn/papers/${paper.id}`);
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Couldn't upload the paper."),
  });
  const remove = useMutation({
    mutationFn: (paper: Paper) => api.deletePaper(token, paper.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: PAPERS_KEY });
      setDoomed(null);
      toast.success("Paper deleted.");
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Couldn't delete the paper."),
  });

  const take = (file: File | undefined) => {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      toast.error("Upload the paper as a PDF.");
      return;
    }
    if (file.size > MAX_BYTES) {
      toast.error("The paper is larger than 25 MB.");
      return;
    }
    upload.mutate(file);
  };

  const onDrop = (event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault();
    setDragging(false);
    take(event.dataTransfer.files?.[0]);
  };

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5 p-4 md:p-6" data-testid="paper-library">
      <header>
        <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
          <FileText className="size-5 text-primary" aria-hidden /> Ask a paper
        </h1>
        <p className="text-sm text-muted-foreground">
          Upload a research paper and ask it anything — a summary, a critical appraisal, what the numbers mean. Answers
          come only from the paper, each with its section and page.
        </p>
      </header>

      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        disabled={upload.isPending || !token}
        className={cn(
          "flex flex-col items-center gap-2 rounded-xl border-2 border-dashed p-8 text-center transition-colors",
          dragging ? "border-primary bg-primary/5" : "hover:border-primary/40 hover:bg-muted/30",
        )}
        data-testid="paper-upload"
      >
        {upload.isPending ? (
          <>
            <Loader2 className="size-7 animate-spin text-primary" aria-hidden />
            <span className="font-medium">Reading the paper, page by page…</span>
            <span className="text-xs text-muted-foreground">Sections and pages are kept, so every answer can say where it came from.</span>
          </>
        ) : (
          <>
            <UploadCloud className="size-7 text-primary" aria-hidden />
            <span className="font-medium">Drop a PDF here, or choose one</span>
            <span className="text-xs text-muted-foreground">
              Up to 25 MB. It is kept in your workspace’s private library — colleagues in your workspace can ask it too.
            </span>
          </>
        )}
      </button>
      <input
        ref={input}
        type="file"
        accept=".pdf,application/pdf"
        className="hidden"
        onChange={(e) => {
          take(e.target.files?.[0]);
          e.target.value = "";
        }}
        data-testid="paper-file"
      />

      <section aria-labelledby="papers-heading" className="flex flex-col gap-2">
        <h2 id="papers-heading" className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Your workspace’s papers
        </h2>
        {papers.isLoading && Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-16" />)}
        {papers.data && papers.data.length === 0 && (
          <p className="flex items-center gap-2 rounded-xl border border-dashed p-5 text-sm text-muted-foreground">
            <FileUp className="size-4" aria-hidden /> No papers yet — upload one above.
          </p>
        )}
        <ul className="flex flex-col gap-2" data-testid="paper-list">
          {papers.data?.map((paper) => (
            <PaperCard
              key={paper.id}
              paper={paper}
              canDelete={paper.uploaded_by_you || role === "owner"}
              onDelete={setDoomed}
            />
          ))}
        </ul>
      </section>

      <Dialog open={doomed !== null} onOpenChange={(open) => !open && setDoomed(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete this paper?</DialogTitle>
            <DialogDescription>
              “{doomed?.title}” is removed from the workspace, with your conversations about it — except any answer saved
              to a binder or shared.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setDoomed(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => doomed && remove.mutate(doomed)}
              disabled={remove.isPending}
              data-testid="paper-delete-confirm"
            >
              {remove.isPending ? "Deleting…" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
