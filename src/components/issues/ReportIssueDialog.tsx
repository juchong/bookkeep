import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Flag, Loader2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { mediaIssuesApi } from '@/lib/api';
import { ISSUE_TYPE_LABELS, MediaFormat, MediaIssueType } from '@/types/issue';

interface ReportIssueDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  bookId?: number;
  bookTitle?: string;
  format?: MediaFormat;
  requestId?: number;
  downloadTaskId?: number;
}

const issueTypes = Object.entries(ISSUE_TYPE_LABELS) as Array<[MediaIssueType, string]>;

export function ReportIssueDialog({
  open,
  onOpenChange,
  bookId,
  bookTitle,
  format: initialFormat,
  requestId,
  downloadTaskId,
}: ReportIssueDialogProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [format, setFormat] = useState<MediaFormat | undefined>(initialFormat);
  const [issueType, setIssueType] = useState<MediaIssueType>(bookId ? 'wrong_language' : 'other');
  const [reportText, setReportText] = useState('');
  const [isCritical, setIsCritical] = useState(false);
  const [criticalExplanation, setCriticalExplanation] = useState('');

  useEffect(() => {
    if (open) {
      setFormat(initialFormat);
      setIssueType(bookId ? 'wrong_language' : 'other');
      setReportText('');
      setIsCritical(false);
      setCriticalExplanation('');
    }
  }, [open, initialFormat, bookId]);

  const createMutation = useMutation({
    mutationFn: () => mediaIssuesApi.create({
      book_id: bookId,
      format,
      issue_type: bookId ? issueType : 'other',
      report_text: reportText.trim(),
      request_id: requestId,
      download_task_id: downloadTaskId,
      page_path: window.location.pathname,
      is_critical: isCritical,
      critical_explanation: isCritical ? criticalExplanation.trim() : undefined,
    }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['media-issues'] });
      queryClient.invalidateQueries({ queryKey: ['admin-media-issues'] });
      toast.success(
        result.already_reported
          ? 'You already reported this problem'
          : result.deduplicated
            ? 'Your report was added to an existing issue'
            : 'Problem reported',
        { description: result.issue.reference },
      );
      onOpenChange(false);
      navigate(`/issues/${result.issue.public_id}`);
    },
    onError: (error: Error) => toast.error('Could not submit report', { description: error.message }),
  });

  const canSubmit = reportText.trim().length >= 5
    && (!bookId || Boolean(format))
    && (!isCritical || criticalExplanation.trim().length >= 5);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg bg-card border-border">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Flag className="h-5 w-5 text-primary" />
            Report a problem
          </DialogTitle>
          <DialogDescription>
            {bookTitle ? `Tell us what is wrong with ${bookTitle}.` : 'Tell us what needs to be fixed.'}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5 py-2">
          {bookId && (
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>Affected format</Label>
                <Select value={format} onValueChange={(value) => setFormat(value as MediaFormat)} disabled={Boolean(initialFormat)}>
                  <SelectTrigger><SelectValue placeholder="Choose a format" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="ebook">Ebook</SelectItem>
                    <SelectItem value="audiobook">Audiobook</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Problem</Label>
                <Select value={issueType} onValueChange={(value) => setIssueType(value as MediaIssueType)}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {issueTypes.map(([value, label]) => (
                      <SelectItem key={value} value={value}>{label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="issue-description">What needs to be fixed?</Label>
            <Textarea
              id="issue-description"
              value={reportText}
              onChange={(event) => setReportText(event.target.value)}
              maxLength={4000}
              rows={5}
              placeholder={
                issueType === 'wrong_language'
                  ? 'Which language did you expect, and which language did you receive?'
                  : issueType === 'file_structure'
                    ? 'Describe how the chapters or files are organized.'
                    : 'Describe the problem and what you expected.'
              }
            />
            <p className="text-xs text-muted-foreground text-right">{reportText.length}/4000</p>
          </div>

          <div className="rounded-xl border border-border p-4 space-y-3">
            <div className="flex items-start gap-3">
              <Checkbox
                id="critical-issue"
                checked={isCritical}
                onCheckedChange={(checked) => setIsCritical(checked === true)}
              />
              <div>
                <Label htmlFor="critical-issue" className="flex items-center gap-2 text-destructive cursor-pointer">
                  <AlertTriangle className="h-4 w-4" />
                  Critical content issue
                </Label>
                <p className="mt-1 text-xs text-muted-foreground">
                  The media contains inappropriate, explicit, or violent content that may have been added accidentally.
                </p>
              </div>
            </div>
            {isCritical && (
              <div className="space-y-2 pl-7">
                <Label htmlFor="critical-explanation">What content did you encounter?</Label>
                <Textarea
                  id="critical-explanation"
                  value={criticalExplanation}
                  onChange={(event) => setCriticalExplanation(event.target.value)}
                  maxLength={1000}
                  rows={3}
                  placeholder="Briefly describe the content so an administrator can identify it."
                />
                <Alert variant="destructive">
                  <AlertDescription>
                    Critical reports are placed at the top of the repair list. The media is not removed automatically.
                  </AlertDescription>
                </Alert>
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={() => createMutation.mutate()} disabled={!canSubmit || createMutation.isPending}>
            {createMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Submit report
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
