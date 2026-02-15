import { useState } from 'react';
import { User, Settings2, Bell, CreditCard } from 'lucide-react';
import { toast } from 'sonner';
import { useAuth } from '../contexts/AuthContext';
import { DashboardLayout } from './DashboardLayout';
import { Tabs, TabsList, TabsTrigger, TabsContent } from './ui/tabs';
import { Card } from './ui/card';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Switch } from './ui/switch';
import { Separator } from './ui/separator';
import {
  Select,
  SelectTrigger,
  SelectContent,
  SelectItem,
  SelectValue,
} from './ui/select';
import { Badge } from './ui/badge';
import { Progress } from './ui/progress';

export function Settings() {
  const { user } = useAuth();

  // Profile state
  const [fullName, setFullName] = useState(user?.full_name || '');

  // Defaults state
  const [exportFormat, setExportFormat] = useState('htaccess');
  const [urlFormat, setUrlFormat] = useState('paths');
  const [highConfidence, setHighConfidence] = useState(true);
  const [mediumConfidence, setMediumConfidence] = useState(true);
  const [lowConfidence, setLowConfidence] = useState(false);
  const [autoApproveHigh, setAutoApproveHigh] = useState(false);

  // Notifications state
  const [emailJobCompleted, setEmailJobCompleted] = useState(true);
  const [emailJobFailed, setEmailJobFailed] = useState(true);
  const [emailWeeklySummary, setEmailWeeklySummary] = useState(false);
  const [desktopNotifications, setDesktopNotifications] = useState(false);
  const [soundOnCompletion, setSoundOnCompletion] = useState(false);

  // Subscription state (mock data)
  const redirectsUsed = 127;
  const redirectsLimit = 1000;
  const usagePercent = Math.round((redirectsUsed / redirectsLimit) * 100);

  // Get user initials for avatar (same logic as TopBar)
  const getInitials = () => {
    const name = user?.full_name;
    if (name) {
      return name
        .split(' ')
        .map((n) => n[0])
        .join('')
        .toUpperCase()
        .slice(0, 2);
    }
    return user?.email?.[0]?.toUpperCase() || 'U';
  };

  return (
    <DashboardLayout title="Settings">
      <div className="max-w-4xl mx-auto w-full">
        <Tabs defaultValue="profile" className="w-full">
          <TabsList className="w-full">
            <TabsTrigger value="profile">
              <User className="h-4 w-4" />
              Profile
            </TabsTrigger>
            <TabsTrigger value="defaults">
              <Settings2 className="h-4 w-4" />
              Defaults
            </TabsTrigger>
            <TabsTrigger value="notifications">
              <Bell className="h-4 w-4" />
              Notifications
            </TabsTrigger>
            <TabsTrigger value="subscription">
              <CreditCard className="h-4 w-4" />
              Subscription
            </TabsTrigger>
          </TabsList>

          {/* ──────────────── Profile Tab ──────────────── */}
          <TabsContent value="profile">
            <Card className="p-6">
              <h2 className="text-lg font-semibold text-foreground">Profile</h2>
              <p className="text-sm text-muted-foreground mb-6">
                Manage your account information.
              </p>

              <Separator />

              <div className="space-y-6 mt-6">
                {/* Avatar */}
                <div className="flex items-center gap-4">
                  <div className="w-16 h-16 shrink-0 rounded-full bg-primary text-primary-foreground flex items-center justify-center font-semibold text-xl">
                    {getInitials()}
                  </div>
                  <div>
                    <p className="font-medium text-foreground">
                      {user?.full_name || 'User'}
                    </p>
                    <p className="text-sm text-muted-foreground">
                      {user?.email || ''}
                    </p>
                  </div>
                </div>

                <Separator />

                {/* Full Name */}
                <div className="space-y-2">
                  <Label htmlFor="fullName">Full Name</Label>
                  <Input
                    id="fullName"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    placeholder="Your full name"
                  />
                </div>

                {/* Email (read-only) */}
                <div className="space-y-2">
                  <Label htmlFor="email">Email</Label>
                  <Input
                    id="email"
                    value={user?.email || ''}
                    disabled
                    readOnly
                  />
                  <p className="text-xs text-muted-foreground">
                    Your email address cannot be changed.
                  </p>
                </div>

                <div className="flex justify-end">
                  <Button
                    onClick={() => toast.success('Profile saved successfully.')}
                  >
                    Save Changes
                  </Button>
                </div>
              </div>
            </Card>
          </TabsContent>

          {/* ──────────────── Defaults Tab ──────────────── */}
          <TabsContent value="defaults">
            <Card className="p-6">
              <h2 className="text-lg font-semibold text-foreground">
                Defaults
              </h2>
              <p className="text-sm text-muted-foreground mb-6">
                Configure default settings for new redirect jobs.
              </p>

              <Separator />

              <div className="space-y-6 mt-6">
                {/* Default Export Format */}
                <div className="space-y-2">
                  <Label htmlFor="exportFormat">Default Export Format</Label>
                  <Select value={exportFormat} onValueChange={setExportFormat}>
                    <SelectTrigger id="exportFormat" className="w-full">
                      <SelectValue placeholder="Select format" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="htaccess">
                        Apache .htaccess
                      </SelectItem>
                      <SelectItem value="nginx">Nginx map</SelectItem>
                      <SelectItem value="wordpress">WordPress CSV</SelectItem>
                      <SelectItem value="vercel">Vercel redirects</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                {/* Default URL Format */}
                <div className="space-y-2">
                  <Label htmlFor="urlFormat">Default URL Format</Label>
                  <Select value={urlFormat} onValueChange={setUrlFormat}>
                    <SelectTrigger id="urlFormat" className="w-full">
                      <SelectValue placeholder="Select format" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="paths">Paths only</SelectItem>
                      <SelectItem value="full">Full URLs</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <Separator />

                {/* Default Confidence Levels */}
                <div className="space-y-4">
                  <Label>Default Confidence Levels</Label>
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-foreground">
                          High
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Include high-confidence matches by default
                        </p>
                      </div>
                      <Switch
                        checked={highConfidence}
                        onCheckedChange={setHighConfidence}
                      />
                    </div>
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-foreground">
                          Medium
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Include medium-confidence matches by default
                        </p>
                      </div>
                      <Switch
                        checked={mediumConfidence}
                        onCheckedChange={setMediumConfidence}
                      />
                    </div>
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-foreground">
                          Low
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Include low-confidence matches by default
                        </p>
                      </div>
                      <Switch
                        checked={lowConfidence}
                        onCheckedChange={setLowConfidence}
                      />
                    </div>
                  </div>
                </div>

                <Separator />

                {/* Auto-approve High Confidence */}
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-foreground">
                      Auto-approve High Confidence
                    </p>
                    <p className="text-xs text-muted-foreground">
                      Automatically approve redirects with high confidence
                      scores without manual review.
                    </p>
                  </div>
                  <Switch
                    checked={autoApproveHigh}
                    onCheckedChange={setAutoApproveHigh}
                  />
                </div>

                <div className="flex justify-end">
                  <Button
                    onClick={() =>
                      toast.success('Default settings saved successfully.')
                    }
                  >
                    Save Defaults
                  </Button>
                </div>
              </div>
            </Card>
          </TabsContent>

          {/* ──────────────── Notifications Tab ──────────────── */}
          <TabsContent value="notifications">
            <Card className="p-6">
              <h2 className="text-lg font-semibold text-foreground">
                Notifications
              </h2>
              <p className="text-sm text-muted-foreground mb-6">
                Choose how and when you want to be notified.
              </p>

              <Separator />

              <div className="space-y-6 mt-6">
                {/* Email Notifications */}
                <div className="space-y-4">
                  <h3 className="text-sm font-semibold text-foreground">
                    Email Notifications
                  </h3>
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-foreground">
                          Job completed
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Receive an email when a redirect job finishes
                          processing.
                        </p>
                      </div>
                      <Switch
                        checked={emailJobCompleted}
                        onCheckedChange={setEmailJobCompleted}
                      />
                    </div>
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-foreground">
                          Job failed
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Receive an email when a redirect job fails or
                          encounters an error.
                        </p>
                      </div>
                      <Switch
                        checked={emailJobFailed}
                        onCheckedChange={setEmailJobFailed}
                      />
                    </div>
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-foreground">
                          Weekly summary
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Receive a weekly digest of your redirect activity.
                        </p>
                      </div>
                      <Switch
                        checked={emailWeeklySummary}
                        onCheckedChange={setEmailWeeklySummary}
                      />
                    </div>
                  </div>
                </div>

                <Separator />

                {/* In-App Notifications */}
                <div className="space-y-4">
                  <h3 className="text-sm font-semibold text-foreground">
                    In-App Notifications
                  </h3>
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-foreground">
                          Show desktop notifications
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Display browser notifications for important events.
                        </p>
                      </div>
                      <Switch
                        checked={desktopNotifications}
                        onCheckedChange={setDesktopNotifications}
                      />
                    </div>
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-foreground">
                          Play sound on completion
                        </p>
                        <p className="text-xs text-muted-foreground">
                          Play an audio cue when a job finishes.
                        </p>
                      </div>
                      <Switch
                        checked={soundOnCompletion}
                        onCheckedChange={setSoundOnCompletion}
                      />
                    </div>
                  </div>
                </div>

                <div className="flex justify-end">
                  <Button
                    onClick={() =>
                      toast.success(
                        'Notification preferences saved successfully.'
                      )
                    }
                  >
                    Save Preferences
                  </Button>
                </div>
              </div>
            </Card>
          </TabsContent>

          {/* ──────────────── Subscription Tab ──────────────── */}
          <TabsContent value="subscription">
            <Card className="p-6">
              <h2 className="text-lg font-semibold text-foreground">
                Subscription
              </h2>
              <p className="text-sm text-muted-foreground mb-6">
                Manage your plan and usage.
              </p>

              <Separator />

              <div className="space-y-6 mt-6">
                {/* Current Plan */}
                <div className="flex items-center justify-between rounded-lg border border-border p-4">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold text-foreground">
                        Free Plan
                      </h3>
                      <Badge variant="secondary">Current</Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      You are on the free tier.
                    </p>
                  </div>
                </div>

                {/* Usage Meter */}
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <Label>Monthly Usage</Label>
                    <span className="text-sm text-muted-foreground">
                      {redirectsUsed} of {redirectsLimit.toLocaleString()}{' '}
                      redirects used this month
                    </span>
                  </div>
                  <Progress value={usagePercent} />
                  <p className="text-xs text-muted-foreground text-right">
                    {usagePercent}% used
                  </p>
                </div>

                <Separator />

                {/* Features List */}
                <div className="space-y-3">
                  <h3 className="text-sm font-semibold text-foreground">
                    Free Tier Features
                  </h3>
                  <ul className="space-y-2 text-sm text-muted-foreground">
                    <li className="flex items-center gap-2">
                      <span className="h-1.5 w-1.5 rounded-full bg-primary shrink-0" />
                      1,000 redirects per month
                    </li>
                    <li className="flex items-center gap-2">
                      <span className="h-1.5 w-1.5 rounded-full bg-primary shrink-0" />
                      3 export formats
                    </li>
                    <li className="flex items-center gap-2">
                      <span className="h-1.5 w-1.5 rounded-full bg-primary shrink-0" />
                      Community support
                    </li>
                  </ul>
                </div>

                <Separator />

                {/* Upgrade */}
                <div className="space-y-2">
                  <Button disabled className="w-full">
                    Upgrade to Pro &mdash; Coming Soon
                  </Button>
                  <p className="text-xs text-muted-foreground text-center">
                    Need more? Contact us for enterprise pricing.
                  </p>
                </div>
              </div>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </DashboardLayout>
  );
}
