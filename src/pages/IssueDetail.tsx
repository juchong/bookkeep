import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ArrowLeft, CheckCircle2, Edit3, Loader2, RotateCcw } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { mediaIssuesApi } from '@/lib/api';
import { ISSUE_TYPE_LABELS } from '@/types/issue';

export default function IssueDetail() {
  const { publicId = '' } = useParams();
  const queryClient = useQueryClient();
  const [editOpen, setEditOpen] = useState(false);
  const [stillBrokenOpen, setStillBrokenOpen] = useState(false);
  const [text, setText] = useState('');
  const { data: issue, isLoading, error } = useQuery({
    queryKey: ['media-issue', publicId],
    queryFn: () => mediaIssuesApi.getById(publicId),
    enabled: Boolean(publicId),
  });
  useEffect(() => setText(issue?.report_text || ''), [issue?.report_text]);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['media-issue', publicId] });
    queryClient.invalidateQueries({ queryKey: ['media-issues'] });
  };
  const updateMutation = useMutation({
    mutationFn: () => mediaIssuesApi.updateReport(publicId, {
      report_text: text.trim(),
      is_critical: issue?.is_critical || false,
      critical_explanation: issue?.critical_explanation || undefined,
    }),
    onSuccess: () => { refresh(); setEditOpen(false); toast.success('Report updated'); },
    onError: (err: Error) => toast.error(err.message),
  });
  const reopenMutation = useMutation({
    mutationFn: () => mediaIssuesApi.stillBroken(publicId, text.trim()),
    onSuccess: () => { refresh(); setStillBrokenOpen(false); toast.success('Issue returned to the repair list'); },
    onError: (err: Error) => toast.error(err.message),
  });

  if (isLoading) return <div className="p-10 text-center text-muted-foreground">Loading issue…</div>;
  if (error || !issue) return <div className="p-10 text-center text-destructive">{(error as Error)?.message || 'Issue not found'}</div>;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Link to="/issues" className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-primary">
        <ArrowLeft className="h-4 w-4" /> Back to issues
      </Link>

      <div className="rounded-2xl border border-border bg-card p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-bold">{issue.book?.title || 'Other Bookkeep problem'}</h1>
              {issue.is_critical && <Badge variant="destructive"><AlertTriangle className="mr-1 h-3 w-3" />Critical</Badge>}
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              {issue.reference} · {issue.format ? `${issue.format === 'ebook' ? 'Ebook' : 'Audiobook'} · ` : ''}
              {ISSUE_TYPE_LABELS[issue.issue_type]}
            </p>
          </div>
          <Badge variant={issue.status === 'done' ? 'default' : 'secondary'} className="capitalize">{issue.status}</Badge>
        </div>

        <div className="mt-6 rounded-xl bg-muted/30 p-4">
          <p className="whitespace-pre-wrap text-sm">{issue.report_text}</p>
        </div>
        {issue.critical_explanation && (
          <div className="mt-4 rounded-xl border border-destructive/40 bg-destructive/5 p-4">
            <p className="text-sm font-medium text-destructive">Critical content</p>
            <p className="mt-1 whitespace-pre-wrap text-sm">{issue.critical_explanation}</p>
          </div>
        )}
        {issue.report_count > 1 && <p className="mt-4 text-sm text-primary">{issue.report_count} people reported this problem.</p>}

        {issue.status === 'done' && (
          <div className="mt-6 rounded-xl border border-success/30 bg-success/5 p-4">
            <div className="flex items-center gap-2 font-medium text-success"><CheckCircle2 className="h-4 w-4" />Marked done</div>
            {issue.done_note && <p className="mt-2 text-sm">{issue.done_note}</p>}
          </div>
        )}

        <div className="mt-6 flex flex-wrap gap-2">
          {issue.status === 'open' ? (
            <Button variant="outline" onClick={() => { setText(issue.report_text || ''); setEditOpen(true); }}>
              <Edit3 className="h-4 w-4" /> Edit details
            </Button>
          ) : (
            <Button variant="outline" onClick={() => { setText(''); setStillBrokenOpen(true); }}>
              <RotateCcw className="h-4 w-4" /> Still a problem
            </Button>
          )}
        </div>
      </div>

      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Edit report details</DialogTitle></DialogHeader>
          <Textarea value={text} onChange={(event) => setText(event.target.value)} rows={6} maxLength={4000} />
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditOpen(false)}>Cancel</Button>
            <Button disabled={text.trim().length < 5 || updateMutation.isPending} onClick={() => updateMutation.mutate()}>
              {updateMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={stillBrokenOpen} onOpenChange={setStillBrokenOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>What is still wrong?</DialogTitle></DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="still-broken">This will return the issue to the admin repair list.</Label>
            <Textarea id="still-broken" value={text} onChange={(event) => setText(event.target.value)} rows={5} maxLength={2000} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setStillBrokenOpen(false)}>Cancel</Button>
            <Button disabled={text.trim().length < 5 || reopenMutation.isPending} onClick={() => reopenMutation.mutate()}>
              {reopenMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Reopen issue
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
