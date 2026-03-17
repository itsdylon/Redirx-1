import { ReactNode, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { CircleUserRound, Menu } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { Button } from './ui/button';
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from './ui/breadcrumb';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from './ui/sheet';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';
import { cn } from './ui/utils';
import { isAgencyPlan } from '../lib/plans';

interface ToolLayoutProps {
  title: string;
  children: ReactNode;
}

export function ToolLayout({ title, children }: ToolLayoutProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const agencyUser = isAgencyPlan(user?.plan);
  const hasSourceSessionId = new URLSearchParams(location.search).has('source_session_id');
  const isUrlToolActive =
    location.pathname === '/url-match' ||
    location.pathname === '/quick-match' ||
    location.pathname.startsWith('/review/') ||
    location.pathname.startsWith('/pricing');
  const isContentToolActive = location.pathname === '/content-match';
  const isToolsActive = isUrlToolActive || isContentToolActive;
  const isProjectHistoryActive = location.pathname === '/projects';
  const showBreadcrumb =
    location.pathname.startsWith('/review/') ||
    (location.pathname === '/pricing' && hasSourceSessionId);
  const breadcrumbLabel = location.pathname.startsWith('/review/')
    ? 'Review Redirects'
    : 'Project Pricing';

  const handleLogout = async () => {
    await logout();
    setMobileNavOpen(false);
    navigate('/login');
  };

  const handlePrimaryNavigate = (path: '/url-match' | '/content-match' | '/projects') => {
    setMobileNavOpen(false);
    navigate(path);
  };

  const navButtonClass = (active: boolean) =>
    cn(
      'text-muted-foreground hover:text-foreground',
      active && 'bg-muted text-foreground',
    );

  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-30 border-b border-border bg-background/95 backdrop-blur">
        <div className="mx-auto w-full max-w-6xl px-6">
          <div className="flex items-center justify-between py-3">
            <div className="flex items-center gap-3">
              <a
                href="https://redirx.dev"
                target="_blank"
                rel="noreferrer"
                className="text-sm font-semibold text-foreground"
              >
                RedirX
              </a>
              <span className="text-sm text-muted-foreground">{title}</span>
            </div>

            <div className="flex items-center gap-2">
              {user ? (
                <>
                  <nav aria-label="Primary" className="hidden items-center gap-1 md:flex">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          size="sm"
                          className={navButtonClass(isToolsActive)}
                          aria-current={isToolsActive ? 'page' : undefined}
                        >
                          Tools
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="start" className="w-64">
                        <DropdownMenuItem onClick={() => handlePrimaryNavigate('/url-match')}>
                          URL Based Matching (Free)
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => handlePrimaryNavigate('/content-match')}>
                          Content Based Matching
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                    <Button
                      variant="ghost"
                      size="sm"
                      className={navButtonClass(isProjectHistoryActive)}
                      aria-current={isProjectHistoryActive ? 'page' : undefined}
                      onClick={() => handlePrimaryNavigate('/projects')}
                    >
                      Project History
                    </Button>
                  </nav>
                  {agencyUser && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="hidden md:inline-flex"
                      onClick={() => navigate('/dashboard')}
                    >
                      Dashboard
                    </Button>
                  )}
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon" aria-label="Open profile menu">
                        <CircleUserRound className="h-5 w-5" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-60">
                      <DropdownMenuLabel className="font-normal">
                        <div className="text-xs text-muted-foreground">Signed in as</div>
                        <div className="truncate text-sm text-foreground">{user.email}</div>
                      </DropdownMenuLabel>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem onClick={handleLogout}>Logout</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>

                  <Sheet open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
                    <SheetTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="md:hidden"
                        aria-label="Open navigation menu"
                      >
                        <Menu className="h-5 w-5" />
                      </Button>
                    </SheetTrigger>
                    <SheetContent side="right">
                      <SheetHeader>
                        <SheetTitle>Navigation</SheetTitle>
                        <SheetDescription>
                          Move between URL Based Matching, Content Based Matching, and project history.
                        </SheetDescription>
                      </SheetHeader>
                      <nav aria-label="Primary mobile" className="flex flex-col gap-1 px-4">
                        <Button
                          variant="ghost"
                          className={cn('justify-start', navButtonClass(isUrlToolActive))}
                          aria-current={isUrlToolActive ? 'page' : undefined}
                          onClick={() => handlePrimaryNavigate('/url-match')}
                        >
                          URL Based Matching
                        </Button>
                        <Button
                          variant="ghost"
                          className={cn('justify-start', navButtonClass(isContentToolActive))}
                          aria-current={isContentToolActive ? 'page' : undefined}
                          onClick={() => handlePrimaryNavigate('/content-match')}
                        >
                          Content Based Matching
                        </Button>
                        <Button
                          variant="ghost"
                          className={cn('justify-start', navButtonClass(isProjectHistoryActive))}
                          aria-current={isProjectHistoryActive ? 'page' : undefined}
                          onClick={() => handlePrimaryNavigate('/projects')}
                        >
                          Project History
                        </Button>
                      </nav>
                      {agencyUser && (
                        <div className="mt-auto px-4 pb-4">
                          <Button
                            variant="outline"
                            className="w-full justify-start"
                            onClick={() => {
                              setMobileNavOpen(false);
                              navigate('/dashboard');
                            }}
                          >
                            Dashboard
                          </Button>
                        </div>
                      )}
                    </SheetContent>
                  </Sheet>
                </>
              ) : (
                <>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="sm">
                        Tools
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-64">
                      <DropdownMenuItem onClick={() => navigate('/url-match')}>
                        URL Based Matching (Free)
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => navigate('/content-match')}>
                        Content Based Matching
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => navigate('/login?redirect=%2Furl-match&source=url-match')}
                  >
                    Log in
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => navigate('/signup?redirect=%2Furl-match&source=url-match')}
                  >
                    Sign up
                  </Button>
                </>
              )}
            </div>
          </div>

          {user && showBreadcrumb && (
            <div className="border-t border-border/60 py-2">
              <Breadcrumb aria-label="Breadcrumb">
                <BreadcrumbList>
                  <BreadcrumbItem>
                    <BreadcrumbLink asChild>
                      <Link to="/url-match">URL Based Matching</Link>
                    </BreadcrumbLink>
                  </BreadcrumbItem>
                  <BreadcrumbSeparator />
                  <BreadcrumbItem>
                    <BreadcrumbPage>{breadcrumbLabel}</BreadcrumbPage>
                  </BreadcrumbItem>
                </BreadcrumbList>
              </Breadcrumb>
            </div>
          )}
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl px-6 py-10">
        {children}
      </main>
    </div>
  );
}
