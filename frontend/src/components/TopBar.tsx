import { useState, useEffect, useRef } from 'react';
import { Bell, User, LogOut, Settings as SettingsIcon, Plus } from 'lucide-react';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';

interface TopBarProps {
  title: string;
  onCreateJob: () => void;
}

export function TopBar({ title, onCreateJob }: TopBarProps) {
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const userName = user?.full_name;
  const userEmail = user?.email;
  const [showNotifications, setShowNotifications] = useState(false);
  const [showProfileMenu, setShowProfileMenu] = useState(false);
  const notificationsRef = useRef<HTMLDivElement>(null);
  const profileRef = useRef<HTMLDivElement>(null);

  // Mock notifications - replace with real data later
  const notifications = [
    { id: 1, message: 'Job "Website Migration" completed', time: '2 min ago', unread: true },
    { id: 2, message: 'New redirect mapping created', time: '1 hour ago', unread: false },
  ];

  const unreadCount = notifications.filter(n => n.unread).length;

  // Close dropdowns when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (notificationsRef.current && !notificationsRef.current.contains(event.target as Node)) {
        setShowNotifications(false);
      }
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

        {/* Notifications */}
        <div className="relative" ref={notificationsRef}>
          <button
            onClick={() => setShowNotifications(!showNotifications)}
            className="relative p-2 rounded-md hover:bg-muted transition-colors"
          >
            <Bell className="h-5 w-5 text-muted-foreground" />
            {unreadCount > 0 && (
              <span className="absolute -top-1 -right-1 w-5 h-5 bg-destructive text-destructive-foreground text-xs rounded-full flex items-center justify-center">
                {unreadCount}
              </span>
            )}
          </button>

          {/* Notifications Dropdown */}
          {showNotifications && (
            <Card className="absolute right-0 top-full mt-2 w-80 shadow-lg z-50">
              <div className="p-4 border-b border-border">
                <h3 className="font-semibold text-foreground">Notifications</h3>
              </div>
              <div className="max-h-96 overflow-y-auto">
                {notifications.length > 0 ? (
                  notifications.map(notification => (
                    <div
                      key={notification.id}
                      className={`p-4 border-b border-border hover:bg-muted cursor-pointer ${
                        notification.unread ? 'bg-muted/50' : ''
                      }`}
                    >
                      <p className="text-sm text-foreground">{notification.message}</p>
                      <p className="text-xs text-muted-foreground mt-1">{notification.time}</p>
                    </div>
                  ))
                ) : (
                  <div className="p-8 text-center text-muted-foreground text-sm">
                    No notifications
                  </div>
                )}
              </div>
            </Card>
          )}
        </div>

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
              <div className="p-3 border-b border-border">
                <p className="font-medium text-foreground text-sm">{userName || 'User'}</p>
                <p className="text-xs text-muted-foreground truncate">{userEmail || ''}</p>
              </div>
              <div className="p-2">
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
