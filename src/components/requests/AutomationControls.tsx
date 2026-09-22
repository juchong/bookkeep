import { useEffect, useId, useState, type ReactNode } from 'react';
import {
  AlertCircle,
  BookOpen,
  Gauge,
  Headphones,
  Languages,
  Loader2,
  Network,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  TimerReset,
  X,
} from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { requestsApi, type AutoDownloadSettings, type AutoDownloadSettingsUpdate } from '@/lib/api';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

type SettingsDraft = Omit<AutoDownloadSettingsUpdate, 'retry_schedule_seconds'> & {
  retryMinutes: number[];
};

type SettingsTab = 'schedule' | 'matching' | 'sources' | 'retries';
type ChoiceKey = 'ebook_formats' | 'audiobook_formats' | 'preferred_languages';

type ChoiceOption = {
  value: string;
  label: string;
  description?: string;
};

type ValidationIssue = {
  message: string;
  tab: SettingsTab;
};

const EBOOK_FORMATS: ChoiceOption[] = [
  { value: 'epub', label: 'EPUB', description: 'Recommended' },
  { value: 'azw3', label: 'AZW3', description: 'Kindle' },
  { value: 'mobi', label: 'MOBI', description: 'Legacy Kindle' },
  { value: 'pdf', label: 'PDF', description: 'Fixed layout' },
  { value: 'cbz', label: 'CBZ', description: 'Comics' },
  { value: 'cbr', label: 'CBR', description: 'Comics' },
];

const AUDIOBOOK_FORMATS: ChoiceOption[] = [
  { value: 'm4b', label: 'M4B', description: 'Recommended' },
  { value: 'mp3', label: 'MP3', description: 'Compatible' },
  { value: 'flac', label: 'FLAC', description: 'Lossless' },
  { value: 'm4a', label: 'M4A' },
  { value: 'aac', label: 'AAC' },
  { value: 'ogg', label: 'OGG' },
  { value: 'opus', label: 'Opus' },
  { value: 'wav', label: 'WAV' },
];

const LANGUAGES: ChoiceOption[] = [
  { value: 'en', label: 'English' },
  { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' },
  { value: 'de', label: 'German' },
  { value: 'it', label: 'Italian' },
  { value: 'pt', label: 'Portuguese' },
  { value: 'nl', label: 'Dutch' },
  { value: 'ja', label: 'Japanese' },
];

const DOWNLOAD_METHODS: ChoiceOption[] = [
  { value: 'usenet', label: 'Usenet', description: 'Send NZBs to the configured Usenet client' },
  { value: 'torrent', label: 'Torrent', description: 'Send magnets or torrent files to qBittorrent' },
];

const STRICTNESS_OPTIONS = [
  {
    value: 'flexible',
    label: 'Broad',
    description: 'Required filters only. Every result that passes them may be downloaded.',
    score: 55,
  },
  {
    value: 'balanced',
    label: 'Standard',
    description: 'Require 70/100. A first-choice format passes; lower choices need stronger release details.',
    score: 70,
  },
  {
    value: 'strict',
    label: 'Conservative',
    description: 'Require 85/100. A first-choice format alone is not enough; it needs additional strong signals.',
    score: 85,
  },
] as const;

function toDraft(settings: AutoDownloadSettingsUpdate): SettingsDraft {
  const { retry_schedule_seconds: retrySchedule, ...rest } = settings;
  return {
    ...rest,
    retryMinutes: retrySchedule.map((value) => value / 60),
  };
}

function optionsWithSavedValues(options: ChoiceOption[], selected: string[]): ChoiceOption[] {
  const known = new Set(options.map((option) => option.value));
  return [
    ...options,
    ...selected
      .filter((value) => !known.has(value))
      .map((value) => ({ value, label: value.toUpperCase(), description: 'Previously saved' })),
  ];
}

function validateDraft(draft: SettingsDraft): ValidationIssue | null {
  if (draft.interval_seconds < 300 || draft.interval_seconds > 604800) {
    return { message: 'Run interval must be between 5 minutes and 7 days.', tab: 'schedule' };
  }
  if (draft.batch_size < 1 || draft.batch_size > 50) {
    return { message: 'Searches per run must be between 1 and 50.', tab: 'schedule' };
  }
  if (draft.max_active_downloads < 1 || draft.max_active_downloads > 100) {
    return { message: 'Maximum active downloads must be between 1 and 100.', tab: 'schedule' };
  }
  if (draft.minimum_score < 0 || draft.minimum_score > 100) {
    return { message: 'Release quality threshold is outside its supported range.', tab: 'matching' };
  }
  if (draft.minimum_seeders < 0 || draft.minimum_seeders > 10000) {
    return { message: 'Minimum seeders must be between 0 and 10,000.', tab: 'matching' };
  }
  if (draft.ebook_min_size_mb < 0 || draft.ebook_max_size_mb <= 0) {
    return { message: 'Ebook size limits must be positive values.', tab: 'matching' };
  }
  if (draft.ebook_min_size_mb >= draft.ebook_max_size_mb) {
    return { message: 'The ebook minimum size must be lower than its maximum size.', tab: 'matching' };
  }
  if (draft.audiobook_min_size_mb < 0 || draft.audiobook_max_size_mb <= 0) {
    return { message: 'Audiobook size limits must be positive values.', tab: 'matching' };
  }
  if (draft.audiobook_min_size_mb >= draft.audiobook_max_size_mb) {
    return { message: 'The audiobook minimum size must be lower than its maximum size.', tab: 'matching' };
  }
  if (draft.retryMinutes.length < 1 || draft.retryMinutes.length > 10) {
    return { message: 'Configure between 1 and 10 retry delays.', tab: 'retries' };
  }
  if (draft.retryMinutes.some((value) => value < 1 || value > 10080)) {
    return { message: 'Each retry delay must be between 1 minute and 7 days.', tab: 'retries' };
  }
  if (draft.retryMinutes.some((value, index) => index > 0 && value < draft.retryMinutes[index - 1])) {
    return { message: 'Retry delays must stay the same or increase over time.', tab: 'retries' };
  }
  return null;
}

function strictnessValue(score: number): string {
  if (score < 60) return 'flexible';
  if (score < 80) return 'balanced';
  return 'strict';
}

export function AutomationControls() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<SettingsTab>('schedule');
  const [draft, setDraft] = useState<SettingsDraft | null>(null);

  const { data: status } = useQuery({
    queryKey: ['request-automation-status'],
    queryFn: requestsApi.getAutomationStatus,
    refetchInterval: 15000,
  });

  const {
    data: settings,
    isError: settingsFailed,
    error: settingsError,
    refetch: refetchSettings,
  } = useQuery({
    queryKey: ['request-automation-settings'],
    queryFn: requestsApi.getAutomationSettings,
    enabled: open,
  });

  useEffect(() => {
    if (open && settings && !draft) setDraft(toDraft(settings));
  }, [draft, open, settings]);

  const handleOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen);
    if (nextOpen) {
      setActiveTab('schedule');
      setDraft(settings ? toDraft(settings) : null);
    } else {
      setDraft(null);
    }
  };

  const saveMutation = useMutation({
    mutationFn: (value: AutoDownloadSettingsUpdate) => requestsApi.updateAutomationSettings(value),
    onSuccess: (saved: AutoDownloadSettings) => {
      queryClient.setQueryData(['request-automation-settings'], saved);
      queryClient.invalidateQueries({ queryKey: ['request-automation-status'] });
      setOpen(false);
      setDraft(null);
      toast.success('Automatic download settings saved');
    },
    onError: (error: Error) => toast.error('Could not save settings', { description: error.message }),
  });

  const runMutation = useMutation({
    mutationFn: (dryRun: boolean) => requestsApi.runAutomation(dryRun),
    onSuccess: (result) => {
      toast.success(result.dry_run ? 'Preview started' : 'Automatic fulfillment started');
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ['request-automation-status'] });
        queryClient.invalidateQueries({ queryKey: ['request-stats'] });
        queryClient.invalidateQueries({ queryKey: ['requests'] });
      }, 1500);
    },
    onError: (error: Error) => toast.error('Could not start fulfillment', { description: error.message }),
  });

  const save = () => {
    if (!draft || validateDraft(draft)) return;
    const payload: AutoDownloadSettingsUpdate = {
      enabled: draft.enabled,
      dry_run: draft.dry_run,
      process_existing_backlog: draft.process_existing_backlog,
      interval_seconds: Number(draft.interval_seconds),
      batch_size: Number(draft.batch_size),
      max_active_downloads: Number(draft.max_active_downloads),
      minimum_score: Number(draft.minimum_score),
      ebook_formats: draft.ebook_formats,
      audiobook_formats: draft.audiobook_formats,
      preferred_languages: draft.preferred_languages,
      protocol_order: draft.protocol_order,
      minimum_seeders: Number(draft.minimum_seeders),
      ebook_min_size_mb: Number(draft.ebook_min_size_mb),
      ebook_max_size_mb: Number(draft.ebook_max_size_mb),
      audiobook_min_size_mb: Number(draft.audiobook_min_size_mb),
      audiobook_max_size_mb: Number(draft.audiobook_max_size_mb),
      retry_schedule_seconds: draft.retryMinutes.map((value) => Math.round(value * 60)),
      categoryless_fallback: draft.categoryless_fallback,
    };
    saveMutation.mutate(payload);
  };

  const setNumber = (key: keyof AutoDownloadSettingsUpdate, value: string) => {
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) return;
    setDraft((current) => current ? { ...current, [key]: numericValue } : current);
  };

  const updateChoice = (key: ChoiceKey, value: string, checked: boolean, options: ChoiceOption[]) => {
    setDraft((current) => {
      if (!current) return current;
      const selected = current[key];
      if (!checked && selected.length === 1) return current;
      const values = checked
        ? [...new Set([...selected, value])]
        : selected.filter((item) => item !== value);
      const order = options.map((option) => option.value);
      values.sort((left, right) => {
        const leftIndex = order.indexOf(left);
        const rightIndex = order.indexOf(right);
        return (leftIndex === -1 ? order.length : leftIndex) - (rightIndex === -1 ? order.length : rightIndex);
      });
      return { ...current, [key]: values };
    });
  };

  const toggleMethod = (method: string, checked: boolean) => {
    setDraft((current) => {
      if (!current) return current;
      if (!checked && current.protocol_order.length === 1) return current;
      const methods = checked
        ? [...current.protocol_order, method]
        : current.protocol_order.filter((value) => value !== method);
      return { ...current, protocol_order: [...new Set(methods)] };
    });
  };

  const preferMethod = (method: string) => {
    setDraft((current) => {
      if (!current || !current.protocol_order.includes(method)) return current;
      return {
        ...current,
        protocol_order: [method, ...current.protocol_order.filter((value) => value !== method)],
      };
    });
  };

  const setRetryMinute = (index: number, value: string) => {
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) return;
    setDraft((current) => {
      if (!current) return current;
      const retryMinutes = [...current.retryMinutes];
      retryMinutes[index] = numericValue;
      return { ...current, retryMinutes };
    });
  };

  const lastSummary = status?.last_run_summary;
  const validationIssue = draft ? validateDraft(draft) : null;

  return (
    <div className="rounded-2xl border border-border/50 bg-card p-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-semibold text-foreground">Automatic fulfillment</h2>
            <Badge variant={status?.enabled ? 'default' : 'secondary'}>{status?.enabled ? 'Enabled' : 'Paused'}</Badge>
            {status?.dry_run && <Badge variant="outline">Preview mode</Badge>}
            {status?.running && <Badge variant="outline">Running</Badge>}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Search approved requests in controlled batches and download releases that meet your rules.
          </p>
          {lastSummary && (
            <p className="mt-2 text-xs text-muted-foreground">
              Last run: {String(lastSummary.claimed ?? 0)} examined, {String(lastSummary.started ?? 0)} started,
              {' '}{String(lastSummary.would_start ?? 0)} preview matches, {String(lastSummary.no_candidate ?? 0)} without a match.
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Dialog open={open} onOpenChange={handleOpenChange}>
            <DialogTrigger asChild>
              <Button variant="outline">
                <Settings2 className="mr-2 h-4 w-4" />
                Configure
              </Button>
            </DialogTrigger>
            <DialogContent className="flex max-h-[92vh] w-[calc(100vw-2rem)] max-w-4xl flex-col gap-0 overflow-hidden p-0">
              <DialogHeader className="shrink-0 border-b border-border/60 px-6 py-5 pr-12 text-left">
                <DialogTitle>Automatic fulfillment settings</DialogTitle>
                <DialogDescription>
                  Set the schedule, matching rules, download methods, and retry behavior.
                </DialogDescription>
              </DialogHeader>

              {!draft && !settingsFailed ? (
                <div className="flex min-h-64 items-center justify-center gap-3 text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin" />
                  Loading settings…
                </div>
              ) : settingsFailed && !draft ? (
                <div className="p-6">
                  <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4" />
                    <AlertDescription className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <span>{settingsError instanceof Error ? settingsError.message : 'Settings could not be loaded.'}</span>
                      <Button type="button" size="sm" variant="outline" onClick={() => refetchSettings()}>
                        Try again
                      </Button>
                    </AlertDescription>
                  </Alert>
                </div>
              ) : draft ? (
                <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as SettingsTab)} className="flex min-h-0 flex-1 flex-col">
                  <div className="shrink-0 border-b border-border/60 px-6 py-3">
                    <TabsList className="grid h-auto w-full grid-cols-4">
                      <TabsTrigger value="schedule" className="gap-2 py-2">
                        <RefreshCw className="hidden h-4 w-4 sm:block" />
                        Run
                      </TabsTrigger>
                      <TabsTrigger value="matching" className="gap-2 py-2">
                        <ShieldCheck className="hidden h-4 w-4 sm:block" />
                        Matching
                      </TabsTrigger>
                      <TabsTrigger value="sources" className="gap-2 py-2">
                        <Network className="hidden h-4 w-4 sm:block" />
                        Sources
                      </TabsTrigger>
                      <TabsTrigger value="retries" className="gap-2 py-2">
                        <TimerReset className="hidden h-4 w-4 sm:block" />
                        Retries
                      </TabsTrigger>
                    </TabsList>
                  </div>

                  <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
                    <TabsContent value="schedule" className="m-0 space-y-6">
                      <SettingsSection
                        icon={<RefreshCw className="h-4 w-4" />}
                        title="Scheduled runs"
                        description="Choose when automation runs and whether scheduled runs preview or download matches."
                      >
                        <div className="grid gap-3 sm:grid-cols-2">
                          <ToggleField
                            label="Automatic runs"
                            description="Run automatically on the interval below"
                            checked={draft.enabled}
                            onCheckedChange={(checked) => setDraft({ ...draft, enabled: checked })}
                          />
                          <ToggleField
                            label="Include existing backlog"
                            description="Include approved requests that already existed when automation was configured"
                            checked={draft.process_existing_backlog}
                            onCheckedChange={(checked) => setDraft({ ...draft, process_existing_backlog: checked })}
                          />
                        </div>
                        <div className="mt-5">
                          <Label className="mb-2 block">Scheduled run mode</Label>
                          <RadioGroup
                            className="grid gap-3 sm:grid-cols-2"
                            value={draft.dry_run ? 'preview' : 'download'}
                            onValueChange={(value) => setDraft({ ...draft, dry_run: value === 'preview' })}
                          >
                            <RadioCard value="preview" label="Preview only" description="Evaluate matches without starting downloads" />
                            <RadioCard value="download" label="Download matches" description="Start the best qualifying release automatically" />
                          </RadioGroup>
                        </div>
                      </SettingsSection>

                      <SettingsSection
                        icon={<Gauge className="h-4 w-4" />}
                        title="Batch limits"
                        description="Limit how often Bookkeep searches and how much work each run may start."
                      >
                        <div className="grid gap-4 sm:grid-cols-3">
                          <NumberField label="Run every" value={draft.interval_seconds / 60} min={5} max={10080} unit="minutes" helper="5 minutes to 7 days" onChange={(value) => setNumber('interval_seconds', String(Number(value) * 60))} />
                          <NumberField label="Searches per run" value={draft.batch_size} min={1} max={50} unit="requests" helper="Approved requests examined per run" onChange={(value) => setNumber('batch_size', value)} />
                          <NumberField label="Max active downloads" value={draft.max_active_downloads} min={1} max={100} unit="downloads" helper="New work waits when this limit is reached" onChange={(value) => setNumber('max_active_downloads', value)} />
                        </div>
                      </SettingsSection>
                    </TabsContent>

                    <TabsContent value="matching" className="m-0 space-y-6">
                      <SettingsSection
                        icon={<BookOpen className="h-4 w-4" />}
                        title="Allowed formats"
                        description="Only releases in the selected formats can be downloaded. Keep at least one format enabled for each media type."
                      >
                        <div className="grid gap-6 lg:grid-cols-2">
                          <ChoiceGroup title="Ebooks" icon={<BookOpen className="h-4 w-4" />} options={optionsWithSavedValues(EBOOK_FORMATS, draft.ebook_formats)} selected={draft.ebook_formats} onCheckedChange={(value, checked) => updateChoice('ebook_formats', value, checked, EBOOK_FORMATS)} />
                          <ChoiceGroup title="Audiobooks" icon={<Headphones className="h-4 w-4" />} options={optionsWithSavedValues(AUDIOBOOK_FORMATS, draft.audiobook_formats)} selected={draft.audiobook_formats} onCheckedChange={(value, checked) => updateChoice('audiobook_formats', value, checked, AUDIOBOOK_FORMATS)} />
                        </div>
                      </SettingsSection>

                      <SettingsSection
                        icon={<ShieldCheck className="h-4 w-4" />}
                        title="Release quality threshold"
                        description="Choose how much evidence a search result needs after it passes the required filters."
                      >
                        <RadioGroup
                          className="grid gap-3 sm:grid-cols-3"
                          value={strictnessValue(draft.minimum_score)}
                          onValueChange={(value) => {
                            const option = STRICTNESS_OPTIONS.find((item) => item.value === value);
                            if (option) setDraft({ ...draft, minimum_score: option.score });
                          }}
                        >
                          {STRICTNESS_OPTIONS.map((option) => (
                            <RadioCard key={option.value} value={option.value} label={option.label} description={option.description} />
                          ))}
                        </RadioGroup>
                        <div className="mt-3 rounded-lg border border-border/60 bg-muted/20 px-4 py-3 text-xs leading-relaxed text-muted-foreground">
                          <p>
                            Before scoring, Bookkeep rejects results outside your selected formats, size ranges, languages, and minimum seeder count.
                          </p>
                          <p className="mt-1.5">
                            Each remaining result starts at 50. It gains 5–20 for format priority, up to 15 for torrent seeders, 10 for a typical file size, 5 for a recent post, 5 for a selected language tag, and 3–5 for download-method preference. This score is calculated per search result; it is not a counter for the settings you select.
                          </p>
                        </div>
                        <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
                          <NumberField label="Minimum torrent seeders" value={draft.minimum_seeders} min={0} max={10000} unit="seeders" helper="Use 0 to allow results without seeders" onChange={(value) => setNumber('minimum_seeders', value)} />
                          <div className="grid gap-4 sm:grid-cols-2">
                            <SizeRange title="Ebook size" minimum={draft.ebook_min_size_mb} maximum={draft.ebook_max_size_mb} onMinimumChange={(value) => setNumber('ebook_min_size_mb', value)} onMaximumChange={(value) => setNumber('ebook_max_size_mb', value)} />
                            <SizeRange title="Audiobook size" minimum={draft.audiobook_min_size_mb} maximum={draft.audiobook_max_size_mb} onMinimumChange={(value) => setNumber('audiobook_min_size_mb', value)} onMaximumChange={(value) => setNumber('audiobook_max_size_mb', value)} />
                          </div>
                        </div>
                      </SettingsSection>

                      <SettingsSection
                        icon={<Languages className="h-4 w-4" />}
                        title="Languages"
                        description="Tagged releases in other languages are rejected. Releases without a language tag remain eligible."
                      >
                        <ChoiceGroup title="Allowed languages" options={optionsWithSavedValues(LANGUAGES, draft.preferred_languages)} selected={draft.preferred_languages} compact onCheckedChange={(value, checked) => updateChoice('preferred_languages', value, checked, LANGUAGES)} />
                      </SettingsSection>
                    </TabsContent>

                    <TabsContent value="sources" className="m-0 space-y-6">
                      <SettingsSection
                        icon={<Network className="h-4 w-4" />}
                        title="Download methods"
                        description="Bookkeep searches every enabled method and ranks all qualifying results together."
                      >
                        <div className="grid gap-3 sm:grid-cols-2">
                          {DOWNLOAD_METHODS.map((method) => {
                            const enabled = draft.protocol_order.includes(method.value);
                            const id = `method-${method.value}`;
                            return (
                              <div key={method.value} className={`flex items-start justify-between gap-4 rounded-xl border p-4 transition-colors ${enabled ? 'border-primary/40 bg-primary/5' : 'border-border/60 bg-muted/20'}`}>
                                <div className="min-w-0">
                                  <Label htmlFor={id} className="cursor-pointer">{method.label}</Label>
                                  <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{method.description}</p>
                                </div>
                                <Switch id={id} checked={enabled} disabled={enabled && draft.protocol_order.length === 1} onCheckedChange={(checked) => toggleMethod(method.value, checked)} />
                              </div>
                            );
                          })}
                        </div>
                        <p className="mt-2 text-xs text-muted-foreground">At least one download method must remain enabled.</p>

                        {draft.protocol_order.length > 1 && (
                          <div className="mt-5 rounded-xl border border-border/60 bg-muted/20 p-4">
                            <Label>Preferred method</Label>
                            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                              This is a small preference when releases are similarly matched. A clearly better release from the other method can still win.
                            </p>
                            <RadioGroup className="mt-3 grid gap-2 sm:grid-cols-2" value={draft.protocol_order[0]} onValueChange={preferMethod}>
                              {DOWNLOAD_METHODS.filter((method) => draft.protocol_order.includes(method.value)).map((method) => (
                                <RadioCard key={`preference-${method.value}`} value={method.value} label={method.label} />
                              ))}
                            </RadioGroup>
                          </div>
                        )}
                      </SettingsSection>

                      <SettingsSection
                        icon={<Search className="h-4 w-4" />}
                        title="Search fallback"
                        description="Control whether Bookkeep makes one broader search after categorized searches return no usable result."
                      >
                        <ToggleField label="Broaden Prowlarr searches" description="Try one final search without a category filter" checked={draft.categoryless_fallback} onCheckedChange={(checked) => setDraft({ ...draft, categoryless_fallback: checked })} />
                      </SettingsSection>
                    </TabsContent>

                    <TabsContent value="retries" className="m-0 space-y-6">
                      <SettingsSection
                        icon={<TimerReset className="h-4 w-4" />}
                        title="Retry delays"
                        description="After an unsuccessful search, Bookkeep waits for the next delay below. The final delay repeats for later attempts."
                      >
                        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                          {draft.retryMinutes.map((minutes, index) => (
                            <div key={`retry-${index}`} className="relative rounded-xl border border-border/60 bg-muted/20 p-4">
                              {draft.retryMinutes.length > 1 && (
                                <Button type="button" variant="ghost" size="icon" className="absolute right-1 top-1 h-8 w-8 text-muted-foreground" aria-label={`Remove retry delay ${index + 1}`} onClick={() => setDraft({ ...draft, retryMinutes: draft.retryMinutes.filter((_, itemIndex) => itemIndex !== index) })}>
                                  <X className="h-3.5 w-3.5" />
                                </Button>
                              )}
                              <NumberField label={`After failed attempt ${index + 1}`} value={minutes} min={1} max={10080} unit="minutes" onChange={(value) => setRetryMinute(index, value)} />
                            </div>
                          ))}
                        </div>
                        {draft.retryMinutes.length < 10 && (
                          <Button type="button" variant="outline" size="sm" className="mt-3" onClick={() => setDraft({ ...draft, retryMinutes: [...draft.retryMinutes, draft.retryMinutes.at(-1) ?? 15] })}>
                            <Plus className="mr-2 h-4 w-4" />
                            Add retry delay
                          </Button>
                        )}
                      </SettingsSection>
                    </TabsContent>
                  </div>
                </Tabs>
              ) : null}

              <DialogFooter className="shrink-0 items-center gap-3 border-t border-border/60 bg-background/95 px-6 py-4 backdrop-blur sm:justify-between sm:space-x-0">
                <div className="min-w-0 flex-1">
                  {validationIssue && (
                    <button type="button" className="flex items-start gap-2 text-left text-xs text-destructive hover:underline" onClick={() => setActiveTab(validationIssue.tab)}>
                      <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      {validationIssue.message}
                    </button>
                  )}
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" onClick={() => handleOpenChange(false)}>Cancel</Button>
                  <Button onClick={save} disabled={!draft || Boolean(validationIssue) || saveMutation.isPending}>
                    {saveMutation.isPending ? 'Saving…' : 'Save settings'}
                  </Button>
                </div>
              </DialogFooter>
            </DialogContent>
          </Dialog>
          <Button variant="outline" onClick={() => runMutation.mutate(true)} disabled={runMutation.isPending || status?.running}>
            <Search className="mr-2 h-4 w-4" />
            Preview batch
          </Button>
          <Button onClick={() => runMutation.mutate(false)} disabled={runMutation.isPending || status?.running}>
            <Play className="mr-2 h-4 w-4" />
            Process now
          </Button>
        </div>
      </div>
    </div>
  );
}

function SettingsSection({ icon, title, description, children }: { icon: ReactNode; title: string; description: string; children: ReactNode }) {
  return (
    <section>
      <div className="mb-4 flex items-start gap-3">
        <div className="mt-0.5 rounded-lg bg-primary/10 p-2 text-primary">{icon}</div>
        <div>
          <h3 className="font-medium text-foreground">{title}</h3>
          <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{description}</p>
        </div>
      </div>
      {children}
    </section>
  );
}

function ToggleField({ label, description, checked, onCheckedChange }: { label: string; description: string; checked: boolean; onCheckedChange: (checked: boolean) => void }) {
  const id = useId();
  return (
    <div className={`flex items-start justify-between gap-4 rounded-xl border p-4 transition-colors ${checked ? 'border-primary/40 bg-primary/5' : 'border-border/60 bg-muted/20'}`}>
      <div className="min-w-0">
        <Label htmlFor={id} className="cursor-pointer">{label}</Label>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{description}</p>
      </div>
      <Switch id={id} checked={checked} onCheckedChange={onCheckedChange} />
    </div>
  );
}

function RadioCard({ value, label, description }: { value: string; label: string; description?: string }) {
  const id = useId();
  return (
    <label htmlFor={id} className="flex cursor-pointer items-start gap-3 rounded-xl border border-border/60 bg-background p-3 transition-colors hover:border-primary/50 has-[[data-state=checked]]:border-primary/50 has-[[data-state=checked]]:bg-primary/5">
      <RadioGroupItem id={id} value={value} className="mt-0.5" />
      <span className="min-w-0">
        <span className="block text-sm font-medium text-foreground">{label}</span>
        {description && <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">{description}</span>}
      </span>
    </label>
  );
}

function ChoiceGroup({ title, icon, options, selected, compact = false, onCheckedChange }: { title: string; icon?: ReactNode; options: ChoiceOption[]; selected: string[]; compact?: boolean; onCheckedChange: (value: string, checked: boolean) => void }) {
  const idPrefix = useId();
  return (
    <fieldset>
      <legend className="mb-2 flex items-center gap-2 text-sm font-medium">
        {icon && <span className="text-muted-foreground">{icon}</span>}
        {title}
      </legend>
      <div className={`grid gap-2 ${compact ? 'grid-cols-2 sm:grid-cols-4' : 'grid-cols-2 sm:grid-cols-3'}`}>
        {options.map((option) => {
          const checked = selected.includes(option.value);
          const id = `${idPrefix}-${option.value}`;
          return (
            <label key={option.value} htmlFor={id} className={`flex cursor-pointer items-start gap-2 rounded-lg border p-3 transition-colors hover:border-primary/50 ${checked ? 'border-primary/40 bg-primary/5' : 'border-border/60 bg-background'}`}>
              <Checkbox id={id} className="mt-0.5" checked={checked} disabled={checked && selected.length === 1} onCheckedChange={(value) => onCheckedChange(option.value, value === true)} />
              <span className="min-w-0">
                <span className="block text-sm font-medium leading-5">{option.label}</span>
                {option.description && <span className="block text-[11px] text-muted-foreground">{option.description}</span>}
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

function SizeRange({ title, minimum, maximum, onMinimumChange, onMaximumChange }: { title: string; minimum: number; maximum: number; onMinimumChange: (value: string) => void; onMaximumChange: (value: string) => void }) {
  return (
    <div className="rounded-xl border border-border/60 bg-muted/20 p-4">
      <Label>{title}</Label>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <NumberField label="Minimum" value={minimum} min={0} unit="MB" step="0.1" onChange={onMinimumChange} />
        <NumberField label="Maximum" value={maximum} min={0.1} unit="MB" step="0.1" onChange={onMaximumChange} />
      </div>
    </div>
  );
}

function NumberField({ label, value, onChange, step = '1', min, max, unit, helper }: { label: string; value: number; onChange: (value: string) => void; step?: string; min?: number; max?: number; unit?: string; helper?: string }) {
  const id = useId();
  const [inputValue, setInputValue] = useState(String(value));
  const [isEditing, setIsEditing] = useState(false);
  const acceptsDecimal = step !== '1';

  useEffect(() => {
    if (!isEditing) setInputValue(String(value));
  }, [isEditing, value]);

  const handleChange = (nextValue: string) => {
    const validNumber = acceptsDecimal ? /^\d*(?:\.\d*)?$/.test(nextValue) : /^\d*$/.test(nextValue);
    if (!validNumber) return;
    setInputValue(nextValue);
    if (nextValue !== '' && nextValue !== '.') onChange(nextValue);
  };

  const handleBlur = () => {
    setIsEditing(false);
    if (inputValue === '' || inputValue === '.') setInputValue(String(value));
  };

  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex min-w-0">
        <Input
          id={id}
          type="text"
          inputMode={acceptsDecimal ? 'decimal' : 'numeric'}
          pattern={acceptsDecimal ? '[0-9]*[.]?[0-9]*' : '[0-9]*'}
          value={inputValue}
          aria-valuemin={min}
          aria-valuemax={max}
          className={unit ? 'min-w-0 rounded-r-none' : undefined}
          onFocus={() => setIsEditing(true)}
          onBlur={handleBlur}
          onChange={(event) => handleChange(event.target.value)}
        />
        {unit && (
          <span className="flex h-10 shrink-0 items-center rounded-r-md border border-l-0 border-input bg-muted/30 px-3 text-xs text-muted-foreground">
            {unit}
          </span>
        )}
      </div>
      {helper && <p className="text-[11px] leading-relaxed text-muted-foreground">{helper}</p>}
    </div>
  );
}
