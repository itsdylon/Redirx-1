import { Menu } from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

interface HeaderProps {
  currentView?: 'dashboard' | 'upload' | 'review' | 'loading';
  onNavigate?: (view: 'dashboard' | 'upload' | 'review') => void;
}

export function Header({ currentView = 'dashboard', onNavigate }: HeaderProps) {
  const handleNavClick = (view: 'dashboard' | 'upload' | 'review') => {
    if (onNavigate) {
      onNavigate(view);
    }
  };



  return (
    <header className="border-b border-gray-300 bg-white">
      <div className="max-w-7xl mx-auto px-8 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-8">
            <button 
              onClick={() => handleNavClick('dashboard')}
              className="text-gray-900 hover:text-gray-700"
            >
              <h1>RedirX</h1>
            </button>
            <nav className="flex gap-6">
              <button
                onClick={() => handleNavClick('dashboard')}
                className={`px-3 py-2 ${
                  currentView === 'dashboard'
                    ? 'text-gray-900 border-b-2 border-gray-900'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                Dashboard
              </button>
              <button
                onClick={() => handleNavClick('upload')}
                className={`px-3 py-2 ${
                  currentView === 'upload'
                    ? 'text-gray-900 border-b-2 border-gray-900'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                Upload
              </button>
              <button
                onClick={() => handleNavClick('review')}
                className={`px-3 py-2 ${
                  currentView === 'review'
                    ? 'text-gray-900 border-b-2 border-gray-900'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                Review
              </button>
            </nav>
          </div>
          <div className="flex items-center gap-4">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button className="inline-flex items-center justify-center h-10 w-10 hover:bg-gray-100 rounded">
                  <Menu className="h-5 w-5" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuItem onClick={() => handleNavClick('dashboard')}>
                  Dashboard
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => handleNavClick('upload')}>
                  New Mapping
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </div>
    </header>
  );
}
