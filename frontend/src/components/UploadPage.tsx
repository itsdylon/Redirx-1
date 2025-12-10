import { uploadCSVs } from "../api/pipeline";
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Header } from './Header';
import { FileUploadZone } from './FileUploadZone';
import { LoadingScreen } from './LoadingScreen';
import { Button } from './ui/button';
import { Toaster } from './ui/sonner';
import { ArrowLeft } from 'lucide-react';

interface FileData {
  name: string;
  rowCount: number;
  file: File;
}

export function UploadPage() {
  const navigate = useNavigate();
  const [isLoading, setIsLoading] = useState(false);
  const [oldSiteFile, setOldSiteFile] = useState<FileData | null>(null);
  const [newSiteFile, setNewSiteFile] = useState<FileData | null>(null);

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

  const handleBeginMatching = async () => {
    if (!oldCsvFile || !newCsvFile) {
      alert("Upload both CSV files first.");
      return;
    }

    setIsLoading(true);

    try {
      const result = await uploadCSVs(oldCsvFile, newCsvFile);

      console.log("Pipeline Response:", result);

      // Navigate to review page with session ID
      if (result.session_id) {
        navigate(`/review/${result.session_id}`);
      }
    } catch (error) {
      console.error(error);
      alert("Error running pipeline.");
      setIsLoading(false);
    }
  };

  const bothFilesUploaded = oldSiteFile && newSiteFile;

  // Show loading screen when processing
  if (isLoading) {
    return (
      <>
        <LoadingScreen />
        <Toaster position="top-right" />
      </>
    );
  }

  return (
    <>
      <div className="min-h-screen bg-gray-50">
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
            <h1 className="text-gray-900 mb-2">Upload CSV Files</h1>
            <p className="text-gray-600">Upload CSV files from your old and new site to begin the redirect mapping process.</p>
          </div>

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
            <div className="mb-8 border border-gray-300 bg-white p-6">
              <div className="grid grid-cols-2 gap-6">
                <div>
                  <div className="text-sm text-gray-600 mb-1">Old Site</div>
                  <div className="text-gray-900">{oldSiteFile.name}</div>
                  <div className="text-sm text-gray-500">{oldSiteFile.rowCount} rows</div>
                </div>
                <div>
                  <div className="text-sm text-gray-600 mb-1">New Site</div>
                  <div className="text-gray-900">{newSiteFile.name}</div>
                  <div className="text-sm text-gray-500">{newSiteFile.rowCount} rows</div>
                </div>
              </div>
            </div>
          )}

          {/* Begin Matching Button */}
          <div>
            <Button
              onClick={handleBeginMatching}
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
