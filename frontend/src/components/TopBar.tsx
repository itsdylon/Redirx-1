import { useState, useEffect, useRef } from 'react';
import { LogOut, Settings as SettingsIcon, Plus, Moon, Sun } from 'lucide-react';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { useTheme } from 'next-themes';

interface TopBarProps {
  title: string;
  onCreateJob: () => void;
}

export function TopBar({ title, onCreateJob }: TopBarProps) {
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const { theme, setTheme } = useTheme();
  const userName = user?.full_name;
  const userEmail = user?.email;
  const [showProfileMenu, setShowProfileMenu] = useState(false);
  const profileRef = useRef<HTMLDivElement>(null);

  // Close dropdowns when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (profileRef.current && !profileRef.current.contains(event.target as Node)) {
        setShowProfileMenu(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Get user initials for avatar
  const getInitials = () => {
    if (userName) {
      return userName
        .split(' ')
        .map(n => n[0])
        .join('')
        .toUpperCase()
        .slice(0, 2);
    }
    return userEmail?.[0]?.toUpperCase() || 'U';
  };

  return (
    <div className="h-16 shrink-0 border-b border-border bg-background flex items-center justify-between px-6">
      {/* Left: Title */}
      <h1 className="text-2xl font-semibold text-foreground">{title}</h1>

      {/* Right: Actions */}
      <div className="flex items-center gap-4">
        {/* Create Job Button */}
        <Button onClick={onCreateJob} size="default">
          <Plus className="h-4 w-4 mr-2" />
          New Redirect Job
        </Button>

        {/* Profile Menu */}
        <div className="relative" ref={profileRef}>
          <button
            onClick={() => setShowProfileMenu(!showProfileMenu)}
            className="flex items-center gap-2 p-1 rounded-md hover:bg-muted transition-colors"
          >
            <div className="w-9 h-9 shrink-0 rounded-full bg-primary text-primary-foreground flex items-center justify-center font-medium text-sm">
              {getInitials()}
            </div>
          </button>

          {/* Profile Dropdown */}
          {showProfileMenu && (
            <Card className="absolute right-0 top-full mt-2 w-56 shadow-lg z-50">
              <div className="p-2 space-y-1">
                <button
                  onClick={() => {
                    navigate('/settings');
                    setShowProfileMenu(false);
                  }}
                  className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                >
                  <SettingsIcon className="h-4 w-4" />
                  Settings
                </button>
                <button
                  onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
                  className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                >
                  {theme === 'dark' ? (
                    <Sun className="h-4 w-4" />
                  ) : (
                    <Moon className="h-4 w-4" />
                  )}
                  {theme === 'dark' ? 'Light Mode' : 'Dark Mode'}
                </button>
                <button
                  onClick={async () => {
                    setShowProfileMenu(false);
                    await logout();
                    navigate('/login');
                  }}
                  className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                >
                  <LogOut className="h-4 w-4" />
                  Logout
                </button>
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
