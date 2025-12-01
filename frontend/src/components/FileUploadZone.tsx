import { useState, useRef } from 'react';
import { Upload, File, X } from 'lucide-react';
import { Button } from './ui/button';

interface FileUploadZoneProps {
  label: string;
  onFileUpload: (file: File) => void;
  file: { name: string; rowCount: number } | null;
}

export function FileUploadZone({ label, onFileUpload, file }: FileUploadZoneProps) {
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => {
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    
    const droppedFile = e.dataTransfer.files[0];
    if (droppedFile && droppedFile.name.endsWith('.csv')) {
      onFileUpload(droppedFile);
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0];
    if (selectedFile) {
      onFileUpload(selectedFile);
    }
  };

  const handleClick = () => {
    fileInputRef.current?.click();
  };

  const handleRemove = (e: React.MouseEvent) => {
    e.stopPropagation();
    // Reset file - in a real app, this would call a parent handler
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  return (
    <div>
      <label className="block mb-2 text-gray-700">{label}</label>
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={handleClick}
        className={`
          border-2 border-dashed p-8 text-center cursor-pointer
          transition-colors min-h-[200px] flex flex-col items-center justify-center
          ${isDragging ? 'border-blue-500 bg-blue-50' : 'border-gray-300 bg-white'}
          ${file ? 'bg-gray-50' : ''}
        `}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv"
          onChange={handleFileSelect}
          className="hidden"
        />
        
        {!file ? (
          <>
            <Upload className="h-12 w-12 text-gray-400 mb-4" />
            <p className="text-gray-700 mb-1">
              Drag and drop CSV file here
            </p>
            <p className="text-gray-500 text-sm mb-4">or</p>
            <Button variant="outline" type="button">
              Browse Files
            </Button>
            <p className="text-gray-400 text-xs mt-4">
              Accepted format: .csv
            </p>
          </>
        ) : (
          <div className="w-full">
            <div className="flex items-center justify-between p-4 border border-gray-300 bg-white">
              <div className="flex items-center gap-3">
                <File className="h-8 w-8 text-gray-600" />
                <div className="text-left">
                  <p className="text-gray-900">{file.name}</p>
                  <p className="text-gray-500 text-sm">{file.rowCount} rows</p>
                </div>
              </div>
              <Button 
                variant="ghost" 
                size="icon"
                onClick={handleRemove}
                type="button"
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
            <p className="text-gray-500 text-sm mt-4">
              Click to replace file
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
