import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, Flag, Plus } from 'lucide-react';
import { Link } from 'react-router-dom';

import { ReportIssueDialog } from '@/components/issues/ReportIssueDialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { mediaIssuesApi } from '@/lib/api';
import { ISSUE_TYPE_LABELS } from '@/types/issue';

const PAGE_SIZE = 20;

export default function Issues() {
  const [status, setStatus] = useState<'open' | 'done'>('open');
  const [page, setPage] = useState(0);
  const [reportOpen, setReportOpen] = useState(false);
  const { data, isLoading, error } = useQuery({
    queryKey: ['media-issues', status, page],
    queryFn: () => mediaIssuesApi.getPage(status, page * PAGE_SIZE, PAGE_SIZE),
  });
  const totalPages = Math.max(1, Math.ceil((data?.total || 0) / PAGE_SIZE));

  return (
    <div className="space-y-7">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="mb-2 flex items-center gap-2 text-sm font-medium uppercase tracking-wider text-primary">
            <Flag className="h-4 w-4" /> Media problems
          </div>
          <h1 className="text-3xl font-bold tracking-tight">My reported issues</h1>
          <p className="mt-2 text-muted-foreground">See what still needs attention and what has been fixed.</p>
        </div>
        <Button onClick={() => setReportOpen(true)}>
          <Plus className="h-4 w-4" /> Report another problem
        </Button>
      </div>

      <Tabs value={status} onValueChange={(value) => { setStatus(value as 'open' | 'done'); setPage(0); }}>
        <TabsList>
          <TabsTrigger value="open">Open</TabsTrigger>
          <TabsTrigger value="done">Done</TabsTrigger>
        </TabsList>
      </Tabs>

      {isLoading ? (
        <div className="rounded-xl border border-border bg-card p-10 text-center text-muted-foreground">Loading issues…</div>
      ) : error ? (
        <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-6 text-destructive">
          {(error as Error).message}
        </div>
      ) : !data?.items.length ? (
        <div className="rounded-xl border border-dashed border-border bg-card/40 p-12 text-center">
          <CheckCircle2 className="mx-auto h-10 w-10 text-muted-foreground/50" />
          <h2 className="mt-4 font-semibold">No {status} issues</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {status === 'open' ? 'You have no media problems waiting to be fixed.' : 'Resolved reports will appear here.'}
          </p>
        </div>
      ) : (
        <div className="grid gap-3">
          {data.items.map((issue) => (
            <Link
              key={issue.public_id}
              to={`/issues/${issue.public_id}`}
              className="flex gap-4 rounded-xl border border-border bg-card p-4 transition-colors hover:border-primary/40"
            >
              <img
                src={issue.book?.cover_url || '/placeholder.svg'}
                alt=""
                className="h-20 w-14 rounded-md object-cover"
              />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold">{issue.book?.title || 'Other Bookkeep problem'}</span>
                  {issue.is_critical && (
                    <Badge variant="destructive"><AlertTriangle className="mr-1 h-3 w-3" />Critical</Badge>
                  )}
                  <Badge variant={issue.status === 'done' ? 'default' : 'secondary'} className="capitalize">
                    {issue.status}
                  </Badge>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">
                  {issue.format ? `${issue.format === 'ebook' ? 'Ebook' : 'Audiobook'} · ` : ''}
                  {ISSUE_TYPE_LABELS[issue.issue_type]} · {issue.reference}
                </p>
                <p className="mt-2 line-clamp-2 text-sm">{issue.report_text}</p>
                {issue.report_count > 1 && (
                  <p className="mt-2 text-xs text-primary">Reported by {issue.report_count} people</p>
                )}
              </div>
            </Link>
          ))}
        </div>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <Button variant="outline" disabled={page === 0} onClick={() => setPage((value) => value - 1)}>
            <ChevronLeft className="h-4 w-4" /> Previous
          </Button>
          <span className="text-sm text-muted-foreground">Page {page + 1} of {totalPages}</span>
          <Button variant="outline" disabled={page + 1 >= totalPages} onClick={() => setPage((value) => value + 1)}>
            Next <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      )}

      <ReportIssueDialog open={reportOpen} onOpenChange={setReportOpen} />
    </div>
  );
}
