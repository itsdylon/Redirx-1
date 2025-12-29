import { Card } from './ui/card';
import { Separator } from './ui/separator';
import { Progress } from './ui/progress';
import { CheckCircle2, AlertCircle } from 'lucide-react';

interface StatsSidebarProps {
  stats: {
    total: number;
    high: number;
    medium: number;
    low: number;
    approved: number;
    approvalProgress: number;
  };
}

export function StatsSidebar({ stats }: StatsSidebarProps) {
  return (
    <aside className="w-80 bg-card border-r border-border p-6 flex-shrink-0">
      <h2 className="text-foreground mb-4">Summary Statistics</h2>

      <Card className="p-4 mb-4">
        <div className="mb-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-foreground">Total Redirects</span>
            <span className="text-foreground">{stats.total}</span>
          </div>
          <Separator />
        </div>

        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 bg-green-500 rounded-full" />
              <span className="text-muted-foreground text-sm">High (≥85%)</span>
            </div>
            <span className="text-foreground">{stats.high}</span>
          </div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 bg-yellow-500 rounded-full" />
              <span className="text-muted-foreground text-sm">Medium (60-85%)</span>
            </div>
            <span className="text-foreground">{stats.medium}</span>
          </div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 bg-red-500 rounded-full" />
              <span className="text-muted-foreground text-sm">Low (&lt;60%)</span>
            </div>
            <span className="text-foreground">{stats.low}</span>
          </div>
        </div>
      </Card>

      <Card className="p-4 mb-4">
        <h3 className="text-foreground text-sm mb-3 flex items-center gap-2">
          <CheckCircle2 className="h-4 w-4 text-green-600" />
          Approval Progress
        </h3>
        <div className="mb-2">
          <div className="flex justify-between text-sm mb-1">
            <span className="text-muted-foreground">{stats.approved} of {stats.total} approved</span>
            <span className="text-foreground">{stats.approvalProgress}%</span>
          </div>
          <Progress value={stats.approvalProgress} className="h-2" />
        </div>
      </Card>

      <div className="pt-6 border-t border-border">
        <h3 className="text-muted-foreground text-sm mb-3">Legend</h3>
        <div className="space-y-2 text-xs text-muted-foreground">
          <div className="flex items-start gap-2">
            <AlertCircle className="h-3 w-3 text-yellow-600 mt-0.5 flex-shrink-0" />
            <span>Near-tie: Multiple similar matches found</span>
          </div>
          <div className="flex items-start gap-2">
            <AlertCircle className="h-3 w-3 text-red-600 mt-0.5 flex-shrink-0" />
            <span>Duplicate target: URL already used</span>
          </div>
          <div className="flex items-start gap-2">
            <AlertCircle className="h-3 w-3 text-orange-600 mt-0.5 flex-shrink-0" />
            <span>Invalid target: Target URL not found</span>
          </div>
        </div>
      </div>
    </aside>
  );
}
