import { uploadCSVs, QuotaExceededError } from "../api/pipeline";
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { DashboardLayout } from './DashboardLayout';
import { FileUploadZone } from './FileUploadZone';
import { LoadingScreen } from './LoadingScreen';
import { Button } from './ui/button';
import { AlertTriangle, Loader2, Zap, Search, Info } from 'lucide-react';
import { validateCSV, CSVValidationResult } from '../utils/validation';

interface FileData {
  name: string;
  rowCount: number;
  file: File;
}

interface QuotaError {
  message: string;
  current_usage: number;
  limit: number;
}

export function UploadPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const isFreeUser = !user?.subscription_plan || user.subscription_plan === 'free';
  const [pipelineType, setPipelineType] = useState<'content' | 'url_only'>(isFreeUser ? 'url_only' : 'content');
  const [isLoading, setIsLoading] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [oldSiteFile, setOldSiteFile] = useState<FileData | null>(null);
  const [newSiteFile, setNewSiteFile] = useState<FileData | null>(null);
  const [quotaError, setQuotaError] = useState<QuotaError | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [duplicateSessionId, setDuplicateSessionId] = useState<string | null>(null);

  // Raw file objects needed for API
  const [oldCsvFile, setOldCsvFile] = useState<File | null>(null);
  const [newCsvFile, setNewCsvFile] = useState<File | null>(null);

  // Validation state
  const [oldFileValidation, setOldFileValidation] = useState<CSVValidationResult | null>(null);
  const [newFileValidation, setNewFileValidation] = useState<CSVValidationResult | null>(null);
  const [pendingWarnings, setPendingWarnings] = useState<{ old: string[], new: string[] } | null>(null);

  const handleFileUpload = async (file: File, type: 'old' | 'new') => {
    // Clear previous errors for this file type
    if (type === 'old') {
      setOldFileValidation(null);
      setOldSiteFile(null);
      setOldCsvFile(null);
    } else {
      setNewFileValidation(null);
      setNewSiteFile(null);
      setNewCsvFile(null);
    }

    // Validate the CSV file
    const validationResult = await validateCSV(file);

    if (type === 'old') {
      setOldFileValidation(validationResult);
    } else {
      setNewFileValidation(validationResult);
    }

    // If validation failed, don't proceed with file upload
    if (!validationResult.valid) {
      return;
    }

    // Read the file content
    const reader = new FileReader();
    reader.onload = (e) => {
      const text = e.target?.result as string;
      const lines = text.split('\n').filter(line => line.trim());

      const fileData: FileData = {
        name: file.name,
        rowCount: validationResult.rowCount,
        file: file,
      };

      if (type === 'old') {
        setOldCsvFile(file);
        setOldSiteFile(fileData);
      } else {
        setNewCsvFile(file);
        setNewSiteFile(fileData);
      }
    };

    reader.readAsText(file);
  };

  const handleBeginMatching = async (force: boolean = false, skipWarningCheck: boolean = false) => {
    if (!oldCsvFile || !newCsvFile) {
      setError("Upload both CSV files first.");
      return;
    }

    // Check for warnings if not already confirmed
    if (!skipWarningCheck) {
      const oldWarnings = oldFileValidation?.warnings || [];
      const newWarnings = newFileValidation?.warnings || [];

      if (oldWarnings.length > 0 || newWarnings.length > 0) {
        setPendingWarnings({ old: oldWarnings, new: newWarnings });
        return;
      }
    }

    // Clear previous errors
    setError(null);
    setQuotaError(null);
    setDuplicateSessionId(null);
    setPendingWarnings(null);
    setIsUploading(true);
    setIsLoading(true);

    try {
      const result = await uploadCSVs(oldCsvFile, newCsvFile, force, isFreeUser ? 'url_only' : pipelineType);

      console.log("Pipeline Response:", result);

      // Check if this is a duplicate run
      if (result.is_duplicate && !force) {
        console.log("Duplicate detected, showing warning");
        setIsUploading(false);
        setIsLoading(false);
        setDuplicateSessionId(result.session_id);
        return;
      }

      // Store session ID - LoadingScreen will poll and navigate when complete
      if (result.session_id) {
        setCurrentSessionId(result.session_id);
      }
      setIsUploading(false);
    } catch (err) {
      console.error(err);
      setIsUploading(false);
      setIsLoading(false);
      setCurrentSessionId(null);

      // Check if it's a quota exceeded error
      if (err && typeof err === 'object' && 'type' in err && (err as QuotaExceededError).type === 'quota_exceeded') {
        const quotaErr = err as QuotaExceededError;
        setQuotaError({
          message: quotaErr.message,
          current_usage: quotaErr.current_usage,
          limit: quotaErr.limit
        });
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("An unexpected error occurred.");
      }
    }
  };

  const handleProceedAnyway = () => {
    handleBeginMatching(true);
  };

  const handleProceedWithWarnings = () => {
    handleBeginMatching(false, true);
  };

  const handleCancelWarnings = () => {
    setPendingWarnings(null);
  };

  const bothFilesUploaded = oldSiteFile && newSiteFile;
  const hasValidationErrors = (oldFileValidation && !oldFileValidation.valid) || (newFileValidation && !newFileValidation.valid);

  // Show loading screen when processing
  if (isLoading) {
    return (
      <DashboardLayout title="Processing">
        <LoadingScreen sessionId={currentSessionId} />
      </DashboardLayout>
    );
  }

  return (
    <DashboardLayout title="Upload CSV Files">
      <div className="max-w-5xl">
          {/* Subtitle */}
          <div className="mb-8">
            <p className="text-muted-foreground">Upload CSV files from your old and new site to begin the redirect mapping process.</p>
          </div>

          {/* Pipeline Type Selector / Tier Banner */}
          {isFreeUser ? (
            <div className="mb-6 border border-blue-500/30 bg-blue-500/5 p-4 flex items-start gap-3">
              <Info className="h-5 w-5 text-blue-500 flex-shrink-0 mt-0.5" />
              <div>
                <div className="font-medium text-blue-600 dark:text-blue-400">Free Plan: URL Matching</div>
                <p className="text-sm text-muted-foreground mt-1">
                  Your free plan uses URL pattern matching (slug comparison, path similarity, and fuzzy matching) to generate redirects. Upgrade for content-based deep matching with AI-powered semantic analysis.
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-3"
                  onClick={() => navigate('/account')}
                >
                  Upgrade Plan
                </Button>
              </div>
            </div>
          ) : (
            <div className="mb-6 grid grid-cols-2 gap-4">
              <button
                onClick={() => setPipelineType('content')}
                className={`border p-4 text-left transition-colors ${
                  pipelineType === 'content'
                    ? 'border-primary bg-primary/5'
                    : 'border-border bg-card hover:bg-accent'
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <Zap className="h-4 w-4 text-primary" />
                  <span className="font-medium text-foreground">Deep Match</span>
                </div>
                <p className="text-sm text-muted-foreground">
                  Scrapes page content and uses AI embeddings for semantic matching. Most accurate.
                </p>
              </button>
              <button
                onClick={() => setPipelineType('url_only')}
                className={`border p-4 text-left transition-colors ${
                  pipelineType === 'url_only'
                    ? 'border-primary bg-primary/5'
                    : 'border-border bg-card hover:bg-accent'
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <Search className="h-4 w-4 text-primary" />
                  <span className="font-medium text-foreground">Quick Match</span>
                </div>
                <p className="text-sm text-muted-foreground">
                  URL pattern matching only. Fastest, no API costs, no scraping required.
                </p>
              </button>
            </div>
          )}

          {/* Quota Exceeded Error */}
          {quotaError && (
            <div className="mb-6 border border-yellow-500 bg-yellow-500/10 p-4 flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 text-yellow-500 flex-shrink-0 mt-0.5" />
              <div>
                <div className="font-medium text-yellow-600 dark:text-yellow-400">Usage Limit Reached</div>
                <p className="text-sm text-muted-foreground mt-1">
                  You've used {quotaError.current_usage} of {quotaError.limit} redirects this month.
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-3"
                  onClick={() => navigate('/account')}
                >
                  View Account & Upgrade
                </Button>
              </div>
            </div>
          )}

          {/* General Error */}
          {error && (
            <div className="mb-6 border border-destructive bg-destructive/10 p-4 text-destructive">
              {error}
            </div>
          )}

          {/* Duplicate Warning */}
          {duplicateSessionId && (
            <div className="mb-6 border border-yellow-500 bg-yellow-500/10 p-4 flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 text-yellow-500 flex-shrink-0 mt-0.5" />
              <div className="flex-1">
                <div className="font-medium text-yellow-600 dark:text-yellow-400">Duplicate Request Detected</div>
                <p className="text-sm text-muted-foreground mt-1">
                  You've already uploaded these exact files before. You can view the existing results or force a new run.
                </p>
                <div className="flex gap-3 mt-3">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => navigate(`/review/${duplicateSessionId}`)}
                  >
                    View Existing Results
                  </Button>
                  <Button
                    variant="default"
                    size="sm"
                    onClick={handleProceedAnyway}
                  >
                    Proceed Anyway
                  </Button>
                </div>
              </div>
            </div>
          )}

          {/* Validation Warnings */}
          {pendingWarnings && (
            <div className="mb-6 border border-yellow-500 bg-yellow-500/10 p-4 flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 text-yellow-500 flex-shrink-0 mt-0.5" />
              <div className="flex-1">
                <div className="font-medium text-yellow-600 dark:text-yellow-400">File Warnings Detected</div>
                <p className="text-sm text-muted-foreground mt-1">
                  The following warnings were found in your files. You can proceed anyway, but processing may take longer or encounter issues.
                </p>
                {pendingWarnings.old.length > 0 && (
                  <div className="mt-3">
                    <div className="text-sm font-medium text-yellow-600 dark:text-yellow-400">Old Site CSV:</div>
                    <ul className="list-disc list-inside text-sm text-muted-foreground mt-1">
                      {pendingWarnings.old.map((warning, idx) => (
                        <li key={idx}>{warning}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {pendingWarnings.new.length > 0 && (
                  <div className="mt-3">
                    <div className="text-sm font-medium text-yellow-600 dark:text-yellow-400">New Site CSV:</div>
                    <ul className="list-disc list-inside text-sm text-muted-foreground mt-1">
                      {pendingWarnings.new.map((warning, idx) => (
                        <li key={idx}>{warning}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <div className="flex gap-3 mt-3">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleCancelWarnings}
                  >
                    Cancel
                  </Button>
                  <Button
                    variant="default"
                    size="sm"
                    onClick={handleProceedWithWarnings}
                  >
                    Proceed Anyway
                  </Button>
                </div>
              </div>
            </div>
          )}

          {/* Upload Zones */}
          <div className="grid grid-cols-2 gap-6 mb-8">
            <FileUploadZone
              label="Old Site CSV"
              onFileUpload={(file) => handleFileUpload(file, 'old')}
              file={oldSiteFile}
              validationError={oldFileValidation && !oldFileValidation.valid ? oldFileValidation.errors.join(', ') : null}
            />
            <FileUploadZone
              label="New Site CSV"
              onFileUpload={(file) => handleFileUpload(file, 'new')}
              file={newSiteFile}
              validationError={newFileValidation && !newFileValidation.valid ? newFileValidation.errors.join(', ') : null}
            />
          </div>

          {/* File Status */}
          {bothFilesUploaded && (
            <div className="mb-8 border border-border bg-card p-6">
              <div className="grid grid-cols-2 gap-6">
                <div>
                  <div className="text-sm text-muted-foreground mb-1">Old Site</div>
                  <div className="text-foreground">{oldSiteFile.name}</div>
                  <div className="text-sm text-muted-foreground">{oldSiteFile.rowCount} rows</div>
                </div>
                <div>
                  <div className="text-sm text-muted-foreground mb-1">New Site</div>
                  <div className="text-foreground">{newSiteFile.name}</div>
                  <div className="text-sm text-muted-foreground">{newSiteFile.rowCount} rows</div>
                </div>
              </div>
            </div>
          )}

          {/* Begin Matching Button */}
          <div>
            <Button
              onClick={() => handleBeginMatching()}
              disabled={!bothFilesUploaded || hasValidationErrors || isUploading}
              size="lg"
              className="w-full"
            >
              {isUploading && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
              {isUploading
                ? 'Processing...'
                : isFreeUser
                  ? 'Begin Quick Match →'
                  : pipelineType === 'content'
                    ? 'Begin Deep Match →'
                    : 'Begin Quick Match →'
              }
            </Button>
            {hasValidationErrors && (
              <p className="text-sm text-destructive mt-2 text-center">
                Please fix validation errors before proceeding
              </p>
            )}
          </div>
      </div>
    </DashboardLayout>
  );
}
