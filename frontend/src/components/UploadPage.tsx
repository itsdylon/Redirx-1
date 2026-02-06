import { uploadCSVs, QuotaExceededError } from "../api/pipeline";
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Header } from './Header';
import { FileUploadZone } from './FileUploadZone';
import { LoadingScreen } from './LoadingScreen';
import { Button } from './ui/button';
import { Toaster } from './ui/sonner';
import { ArrowLeft, AlertTriangle } from 'lucide-react';

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
  const [isLoading, setIsLoading] = useState(false);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [oldSiteFile, setOldSiteFile] = useState<FileData | null>(null);
  const [newSiteFile, setNewSiteFile] = useState<FileData | null>(null);
  const [quotaError, setQuotaError] = useState<QuotaError | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [duplicateSessionId, setDuplicateSessionId] = useState<string | null>(null);

  // Raw file objects needed for API
  const [oldCsvFile, setOldCsvFile] = useState<File | null>(null);
  const [newCsvFile, setNewCsvFile] = useState<File | null>(null);

  const handleFileUpload = (file: File, type: 'old' | 'new') => {
    const reader = new FileReader();
    reader.onload = (e) => {
      const text = e.target?.result as string;
      const lines = text.split('\n').filter(line => line.trim());

      const fileData: FileData = {
        name: file.name,
        rowCount: lines.length - 1,
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

  const handleBeginMatching = async (force: boolean = false) => {
    if (!oldCsvFile || !newCsvFile) {
      setError("Upload both CSV files first.");
      return;
    }

    // Clear previous errors
    setError(null);
    setQuotaError(null);
    setDuplicateSessionId(null);
    setIsLoading(true);

    try {
      const result = await uploadCSVs(oldCsvFile, newCsvFile, force);

      console.log("Pipeline Response:", result);

      // Check if this is a duplicate run
      if (result.is_duplicate && !force) {
        console.log("Duplicate detected, showing warning");
        setIsLoading(false);
        setDuplicateSessionId(result.session_id);
        return;
      }

      // Store session ID - LoadingScreen will poll and navigate when complete
      if (result.session_id) {
        setCurrentSessionId(result.session_id);
      }
    } catch (err) {
      console.error(err);
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

  const bothFilesUploaded = oldSiteFile && newSiteFile;

  // Show loading screen when processing
  if (isLoading) {
    return (
      <>
        <LoadingScreen sessionId={currentSessionId} />
        <Toaster position="top-right" />
      </>
    );
  }

  return (
    <>
      <div className="min-h-screen">
        <Header currentView="upload" />

        <main className="max-w-7xl mx-auto p-8">
          {/* Back to Dashboard */}
          <div className="mb-6">
            <Button variant="outline" onClick={() => navigate('/')}>
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Dashboard
            </Button>
          </div>

          {/* Page Title */}
          <div className="mb-8">
            <h1 className="text-foreground mb-2">Upload CSV Files</h1>
            <p className="text-muted-foreground">Upload CSV files from your old and new site to begin the redirect mapping process.</p>
          </div>

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

          {/* Upload Zones */}
          <div className="grid grid-cols-2 gap-6 mb-8">
            <FileUploadZone
              label="Old Site CSV"
              onFileUpload={(file) => handleFileUpload(file, 'old')}
              file={oldSiteFile}
            />
            <FileUploadZone
              label="New Site CSV"
              onFileUpload={(file) => handleFileUpload(file, 'new')}
              file={newSiteFile}
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
              disabled={!bothFilesUploaded}
              size="lg"
              className="w-full"
            >
              Begin Matching →
            </Button>
          </div>
        </main>
      </div>
      <Toaster position="top-right" />
    </>
  );
}
