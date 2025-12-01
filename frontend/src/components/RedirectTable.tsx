import React from 'react';
import { ChevronDown, ChevronRight, Edit2, AlertTriangle, CheckCircle } from 'lucide-react';
import { Checkbox } from './ui/checkbox';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from './ui/table';
import { RedirectMapping } from './ReviewInterface';
import { Separator } from './ui/separator';

interface RedirectTableProps {
  redirects: RedirectMapping[];
  selectedRows: Set<string>;
  expandedRow: string | null;
  onToggleSelect: (id: string) => void;
  onToggleExpand: (id: string) => void;
  onEdit: (redirect: RedirectMapping) => void;
}

export function RedirectTable({
  redirects,
  selectedRows,
  expandedRow,
  onToggleSelect,
  onToggleExpand,
  onEdit,
}: RedirectTableProps) {
  const getConfidenceBadge = (band: string) => {
    switch (band) {
      case 'high':
        return <Badge className="bg-green-100 text-green-800 border-green-300">High</Badge>;
      case 'medium':
        return <Badge className="bg-yellow-100 text-yellow-800 border-yellow-300">Medium</Badge>;
      case 'low':
        return <Badge className="bg-red-100 text-red-800 border-red-300">Low</Badge>;
      default:
        return null;
    }
  };

  const getWarningIcons = (warnings: string[]) => {
    return warnings.map((warning, index) => {
      let color = 'text-yellow-600';
      let title = warning;
      
      if (warning === 'duplicate-target') {
        color = 'text-red-600';
        title = 'Duplicate target';
      } else if (warning === 'invalid-target') {
        color = 'text-orange-600';
        title = 'Invalid target';
      } else if (warning === 'near-tie') {
        color = 'text-yellow-600';
        title = 'Near-tie match';
      }

      return (
        <AlertTriangle
          key={index}
          className={`h-4 w-4 ${color}`}
          title={title}
        />
      );
    });
  };

  return (
    <div className="border border-gray-300 bg-white">
      <Table>
        <TableHeader>
          <TableRow className="bg-gray-100">
            <TableHead className="w-12"></TableHead>
            <TableHead className="w-12">
              <Checkbox />
            </TableHead>
            <TableHead className="text-gray-900">Old URL</TableHead>
            <TableHead className="text-gray-900">Suggested New URL</TableHead>
            <TableHead className="text-gray-900 w-32">Confidence</TableHead>
            <TableHead className="text-gray-900 w-24 text-center">Score</TableHead>
            <TableHead className="w-20 text-gray-900 text-center">Status</TableHead>
            <TableHead className="w-16 text-gray-900 text-center">Warnings</TableHead>
            <TableHead className="w-16"></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {redirects.map((redirect) => (
            <React.Fragment key={redirect.id}>
              <TableRow
                className={`
                  ${redirect.approved ? 'bg-green-50' : ''}
                  ${redirect.confidenceBand === 'high' ? 'border-l-4 border-l-green-500' : ''}
                  ${redirect.confidenceBand === 'medium' ? 'border-l-4 border-l-yellow-500' : ''}
                  ${redirect.confidenceBand === 'low' ? 'border-l-4 border-l-red-500' : ''}
                `}
              >
                <TableCell>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => onToggleExpand(redirect.id)}
                  >
                    {expandedRow === redirect.id ? (
                      <ChevronDown className="h-4 w-4" />
                    ) : (
                      <ChevronRight className="h-4 w-4" />
                    )}
                  </Button>
                </TableCell>
                <TableCell>
                  <Checkbox
                    checked={selectedRows.has(redirect.id)}
                    onCheckedChange={() => onToggleSelect(redirect.id)}
                  />
                </TableCell>
                <TableCell className="text-gray-700 font-mono text-sm">
                  {redirect.oldUrl}
                </TableCell>
                <TableCell className="text-gray-700 font-mono text-sm">
                  {redirect.newUrl}
                </TableCell>
                <TableCell>
                  {getConfidenceBadge(redirect.confidenceBand)}
                </TableCell>
                <TableCell className="text-center">
                  <span className="text-gray-900">{redirect.matchScore}%</span>
                </TableCell>
                <TableCell className="text-center">
                  {redirect.approved ? (
                    <CheckCircle className="h-5 w-5 text-green-600 mx-auto" />
                  ) : (
                    <span className="text-gray-400 text-sm">Pending</span>
                  )}
                </TableCell>
                <TableCell>
                  <div className="flex items-center justify-center gap-1">
                    {getWarningIcons(redirect.warnings)}
                  </div>
                </TableCell>
                <TableCell>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => onEdit(redirect)}
                  >
                    <Edit2 className="h-4 w-4" />
                  </Button>
                </TableCell>
              </TableRow>

              {/* Expanded Row Details */}
              {expandedRow === redirect.id && (
                <TableRow className="bg-gray-50">
                  <TableCell colSpan={9} className="p-6">
                    <div className="max-w-3xl">
                      <h3 className="text-gray-900 mb-4">Matching Details</h3>
                      <div className="grid grid-cols-3 gap-6">
                        <div className="border border-gray-300 bg-white p-4">
                          <div className="text-gray-700 text-sm mb-2">Path Similarity</div>
                          <div className="flex items-end gap-2">
                            <span className="text-gray-900 text-2xl">{redirect.pathSimilarity}%</span>
                          </div>
                          <div className="mt-2 h-2 bg-gray-200 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-blue-500"
                              style={{ width: `${redirect.pathSimilarity}%` }}
                            />
                          </div>
                        </div>

                        <div className="border border-gray-300 bg-white p-4">
                          <div className="text-gray-700 text-sm mb-2">Title Similarity</div>
                          <div className="flex items-end gap-2">
                            <span className="text-gray-900 text-2xl">{redirect.titleSimilarity}%</span>
                          </div>
                          <div className="mt-2 h-2 bg-gray-200 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-blue-500"
                              style={{ width: `${redirect.titleSimilarity}%` }}
                            />
                          </div>
                        </div>

                        <div className="border border-gray-300 bg-white p-4">
                          <div className="text-gray-700 text-sm mb-2">Content Similarity</div>
                          <div className="flex items-end gap-2">
                            <span className="text-gray-900 text-2xl">{redirect.contentSimilarity}%</span>
                          </div>
                          <div className="mt-2 h-2 bg-gray-200 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-blue-500"
                              style={{ width: `${redirect.contentSimilarity}%` }}
                            />
                          </div>
                        </div>
                      </div>

                      {redirect.warnings.length > 0 && (
                        <div className="mt-4 p-4 border border-yellow-300 bg-yellow-50">
                          <h4 className="text-gray-900 text-sm mb-2">⚠️ Warnings</h4>
                          <ul className="text-sm text-gray-700 space-y-1">
                            {redirect.warnings.map((warning, index) => (
                              <li key={index} className="list-disc list-inside">
                                {warning === 'duplicate-target' && 'This URL is already assigned to another redirect'}
                                {warning === 'invalid-target' && 'Target URL does not exist in new site'}
                                {warning === 'near-tie' && 'Multiple URLs have similar confidence scores'}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}

                      <div className="mt-4 flex gap-3">
                        <Button variant="default" size="sm">
                          Approve Match
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => onEdit(redirect)}>
                          Edit Mapping
                        </Button>
                        <Button variant="outline" size="sm">
                          View Alternatives
                        </Button>
                      </div>
                    </div>
                  </TableCell>
                </TableRow>
              )}
            </React.Fragment>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
