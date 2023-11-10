from django.utils import timezone
from .models import Match

from datetime import datetime
import requests
import re
from bs4 import BeautifulSoup

def refresh_matches(get_response):

    def middleware(request):
        '''
        This procedure contains logic to minimize scraps of flashscore.mobi app (Let's call this API call optimization)
        '''
        # If we are doing something in the admin panel do not refresh the scores...
        # Also do not refresh the scores while making changes to the database (importing predictions)
        if 'django-administration-panel' in request.path or 'refresh' in request.path:
            return get_response(request)

        # Return fixtures that should have started but status is not finished
        ongoing_matches = Match.objects.filter(match_time__lte=timezone.now()).exclude(status__exact='F')

        # Iterate throughout all the ongoing fixtures
        for match in ongoing_matches:
                # Scrape mobile URL
                urlm = f'http://www.flashscore.mobi/match/{match.matchid}/'
                r = requests.get(urlm)

                # SOUP-IT!
                soup = BeautifulSoup(r.text, 'html.parser')

                # Get home and away team
                home_team, away_team = soup.find("h3").get_text().split(' - ')
        
                # Determine fixture status from <div> tags
                # Order of <div>s: 
                # running: score, status (minutes), fixture's datetime and date OR
                # finished: score (HT, FT), Finished, fixture's datetime OR score (HT, FT), Finished, information, fixture's datetime
                # scheduled: fixture's datetime OR fixture's datetime, information
                # postponed: Postponed, fixture's datetime OR Postponed, information, fixture's datetime

                score_pattern = re.compile(r'(?:<span class="live">)*(?:<b>)(\d+):(\d+)(?:<\/b>)(?:<\/span>)*\s*(?:\((\d+):(\d+),(\d+):(\d+)\))*')
                datetime_pattern = re.compile(r'(\d+\.\d+.\d+ \d+:\d+)')
                status_pattern = re.compile(r'(?:1st )*(?:2nd )*Half - (\d+)([+]*)\\\'')
    
                divs = soup.find_all("div", attrs={'class':'detail'})
                for div in divs[:4]:
                    contents = str(div.encode())
                    if mobj := score_pattern.search(contents):
                        # Fixture in play or finished, we know the score
                        match.home_goals = mobj.group(1)
                        match.away_goals = mobj.group(2)
                    if mobj := datetime_pattern.search(contents):
                        # Div represents the fixture's date
                        match.match_time = timezone.make_aware(datetime.strptime(mobj.group(1),'%d.%m.%Y %H:%M'), timezone.get_default_timezone())
                        if match.match_time > timezone.now():
                            match_status = 'S'
                    if mobj := status_pattern.search(contents):
                        # Fixture is in play
                        match.minutes = mobj.group(1)
                        match_status = 'L'
                    if div.get_text() == 'Finished':
                        # Fixture is finished
                        match.minutes = 0
                        match_status = 'F'
                    if div.get_text() == 'Postponed':
                        # Fixture is postponed
                        match_status = 'P'
                    if div.get_text() == 'Half Time':
                        # At HT we set minutes to -1
                        match_minutes = -1
                    else:
                        # We don't care about information tags
                        pass

                match.status = match_status
                # Push changes to the db
                match.save()

        response = get_response(request)

        return response

    return middleware