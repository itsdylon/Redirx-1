import { useState } from 'react';
import { X } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { RedirectMapping } from './ReviewInterface';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';

interface InlineEditDialogProps {
  redirect: RedirectMapping;
  onSave: (redirect: RedirectMapping) => void;
  onCancel: () => void;
}

export function InlineEditDialog({ redirect, onSave, onCancel }: InlineEditDialogProps) {
  const [editedRedirect, setEditedRedirect] = useState(redirect);

  const handleSave = () => {
    onSave(editedRedirect);
  };

  return (
    <Dialog open={true} onOpenChange={onCancel}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Edit Redirect Mapping</DialogTitle>
          <DialogDescription>
            Modify the redirect mapping details below. Changes will be reflected in the mapping table.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6 py-4">
          {/* Old URL */}
          <div className="space-y-2">
            <Label htmlFor="old-url" className="text-gray-900">
              Old URL
            </Label>
            <Input
              id="old-url"
              value={editedRedirect.oldUrl}
              onChange={(e) => setEditedRedirect({ ...editedRedirect, oldUrl: e.target.value })}
              className="font-mono text-sm"
              disabled
            />
            <p className="text-xs text-gray-500">Source URL cannot be modified</p>
          </div>

          {/* New URL */}
          <div className="space-y-2">
            <Label htmlFor="new-url" className="text-gray-900">
              New Target URL
            </Label>
            <Input
              id="new-url"
              value={editedRedirect.newUrl}
              onChange={(e) => setEditedRedirect({ ...editedRedirect, newUrl: e.target.value })}
              className="font-mono text-sm"
              placeholder="/new/url/path"
            />
            <p className="text-xs text-gray-500">Enter the correct target URL for this redirect</p>
          </div>

          {/* Match Score Override */}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="match-score" className="text-gray-900">
                Manual Confidence Override
              </Label>
              <Select
                value={editedRedirect.confidenceBand}
                onValueChange={(value: 'high' | 'medium' | 'low') =>
                  setEditedRedirect({ ...editedRedirect, confidenceBand: value })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="high">High Confidence</SelectItem>
                  <SelectItem value="medium">Medium Confidence</SelectItem>
                  <SelectItem value="low">Low Confidence</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label className="text-gray-900">Current Match Score</Label>
              <div className="flex items-center h-10 px-3 border border-gray-300 rounded-md bg-gray-50">
                <span className="text-gray-900">{editedRedirect.matchScore}%</span>
              </div>
            </div>
          </div>

          {/* Alternative Suggestions */}
          <div className="border border-gray-300 bg-gray-50 p-4">
            <h4 className="text-gray-900 text-sm mb-3">Alternative Matches</h4>
            <div className="space-y-2">
              <button className="w-full text-left p-2 border border-gray-300 bg-white hover:bg-gray-50 transition-colors text-sm font-mono">
                /solutions/consulting-services <span className="text-gray-500 float-right">82%</span>
              </button>
              <button className="w-full text-left p-2 border border-gray-300 bg-white hover:bg-gray-50 transition-colors text-sm font-mono">
                /services/advisory <span className="text-gray-500 float-right">76%</span>
              </button>
              <button className="w-full text-left p-2 border border-gray-300 bg-white hover:bg-gray-50 transition-colors text-sm font-mono">
                /professional-services <span className="text-gray-500 float-right">71%</span>
              </button>
            </div>
            <p className="text-xs text-gray-500 mt-2">Click an alternative to use it as the new target</p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <Button onClick={handleSave}>
            Save Changes
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
