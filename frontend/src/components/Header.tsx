import { useNavigate, useLocation } from 'react-router-dom';
import { Settings, LogOut } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { Avatar, AvatarFallback } from './ui/avatar';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';
import { ThemeToggle } from './ThemeToggle';

interface HeaderProps {
  currentView?: 'dashboard' | 'upload' | 'review' | 'loading' | 'account';
}

export function Header({ currentView }: HeaderProps) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  // Determine current view from URL if not provided
  const getCurrentView = (): string => {
    if (currentView) return currentView;
    const path = location.pathname;
    if (path === '/') return 'dashboard';
    if (path === '/upload') return 'upload';
    if (path.startsWith('/review')) return 'review';
    if (path === '/account') return 'account';
    return 'dashboard';
  };

  const activeView = getCurrentView();

  const handleNavClick = (path: string) => {
    navigate(path);
  };

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  const getInitials = (name?: string, email?: string): string => {
    if (name) {
      return name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);
    }
    return email ? email[0].toUpperCase() : 'U';
  };

  return (
    <header className="border-b border-border bg-card">
      <div className="max-w-7xl mx-auto px-8 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-8">
            <button
              onClick={() => handleNavClick('/')}
              className="text-foreground hover:text-foreground/80"
            >
              <h1>RedirX</h1>
            </button>
            <nav className="flex gap-6">
              <button
                onClick={() => handleNavClick('/')}
                className={`px-3 py-2 ${
                  activeView === 'dashboard'
                    ? 'text-foreground border-b-2 border-foreground'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                Dashboard
              </button>
              <button
                onClick={() => handleNavClick('/upload')}
                className={`px-3 py-2 ${
                  activeView === 'upload'
                    ? 'text-foreground border-b-2 border-foreground'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                Upload
              </button>
              <button
                onClick={() => handleNavClick('/')}
                className={`px-3 py-2 ${
                  activeView === 'review'
                    ? 'text-foreground border-b-2 border-foreground'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
                disabled={activeView !== 'review'}
                title={activeView !== 'review' ? 'Upload files first to access Review' : undefined}
              >
                Review
              </button>
            </nav>
          </div>
          <div className="flex items-center gap-4">
            <ThemeToggle />
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button className="flex items-center gap-2 hover:bg-accent rounded-lg px-3 py-2">
                  <Avatar className="!size-8">
                    <AvatarFallback className="bg-muted text-muted-foreground text-sm font-medium">
                      {getInitials(user?.full_name, user?.email)}
                    </AvatarFallback>
                  </Avatar>
                  <span className="text-sm text-muted-foreground hidden md:block">
                    {user?.full_name || user?.email}
                  </span>
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuLabel>
                  <div className="flex flex-col">
                    <span className="font-medium">{user?.full_name || 'User'}</span>
                    <span className="text-xs text-muted-foreground font-normal">{user?.email}</span>
                  </div>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => navigate('/account')}>
                  <Settings className="mr-2 h-4 w-4" />
                  Account Settings
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={handleLogout} className="text-destructive">
                  <LogOut className="mr-2 h-4 w-4" />
                  Logout
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </div>
    </header>
  );
}
