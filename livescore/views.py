from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.shortcuts import redirect
from django.db.models import Min, Max, Sum
from .models import Match, Prediction, User
from .forms import UploadFileForm

from datetime import datetime
import requests, zipfile, io, csv, re
from bs4 import BeautifulSoup
import time

import json


def livescores(request):
    '''
        This view returns live ranking
    '''
    # Find for live match
    start = time.perf_counter()
    live_games = Match.objects.filter(status__exact='L')

    # Get users
    users = Prediction.objects.values('user').order_by('user').annotate(points = Sum('points')).order_by('-points')
    live_points = [0]*len(users)

    for i, user in enumerate(users):
        # Filter live users predictions
        live_matches = Prediction.objects.filter(user__userid__exact=user['user']).filter(match__status__exact='L')
        # Determine the sum of points (live) for each rank
        live_points[i] = sum([match.live_points for match in live_matches])
        
    # Sort all users by points (descending)
    live_sorted = sorted(range(len(live_points)), key=lambda i: users[i]['points']+live_points[i], reverse=True)
    ranking_data = [{
        'user': User.objects.get(pk=users[past_rank]['user']),
        'points': users[past_rank]['points']+live_points[past_rank],
        'change': live_rank-past_rank,
        'points_live': live_points[past_rank]
        } for live_rank, past_rank in enumerate(live_sorted)]

    return render(request, 'livescore/livescores.html', {
        'page_title': 'Livescores',
        'live_games': live_games,
        'rankings': ranking_data,
        'render_time': f'{(time.perf_counter()-start)*1000:.0f} ms'
    })

def fixtures(request):
    '''
        This view lists all Match(es) split (filteres) by the round (GameWeek)
    '''
    # Initialize matches dictionary {round: match QuerySet}
    matches = {}
    last_gw = 0
    # Filter matches for the given round.
    for i in range(1, 39):
        matches[i] = Match.objects.filter(round__exact=i).order_by('match_time')
        # If all fixtures are finished, GW is finished
        if len(matches[i].filter(match_time__lte=timezone.now()+timezone.timedelta(hours=2)))==10:
            last_gw = i

    return render(request, 'livescore/fixtures.html', {
        'page_title': 'Fixtures',
        'matches': matches,
        'current_gw': last_gw+1
    })

def user(request, userid=''):
    '''
        This view lists all Prediction(s) for the given User.
    '''
    user = User.objects.get(pk=userid)
    max_round = Prediction.objects.aggregate(Max('match__round'))['match__round__max']
    predictions = [Prediction.objects.filter(user__exact=userid).filter(match__round__exact=i+1).exclude(match__status__exact='P') for i in range(max_round)]
    total = Prediction.objects.filter(user__exact=userid).aggregate(Sum('points'))['points__sum']

    return render(request, 'livescore/user.html', {
        'page_title': f'User {user}',
        'user': user,
        'total': total,
        'predictions': predictions
    })


def match(request, matchid=''):
    '''
        This view lists all Prediction(s) for the given Match and provides a basic data if the fixture is ongoing.
    '''
    match = Match.objects.get(pk=matchid)
    predictions = Prediction.objects.filter(match__exact=matchid)
    
    return render(request, 'livescore/match.html', {
        'page_title': f'Match {match}',
        'match': match,
        'predictions': predictions
    })

def privacy(request):
    '''
        Mandatory privacy policy.
    '''
    return render(request, 'livescore/privacy.html', {
        'page_title': 'About | Privacy policy',
    })

def deadlines(request):
    '''
        This view lists all deadlines by filtering Match(es) table by round (GameWeek)
    '''
    # Initialize deadlines dictionary {round: [match_time, has_passed]}
    deadlines = {}
    # Filter matches for the given round (GW), find the earliest time/date ('match_time__min')
    for i in range(38):
        deadlines[i+1] = [  Match.objects.filter(round__exact=i+1).aggregate(Min('match_time'))['match_time__min'],
                            Match.objects.filter(round__exact=i+1).aggregate(Min('match_time'))['match_time__min']<timezone.now()]
    return render(request, 'livescore/deadlines.html', {
        'page_title': 'Deadlines',
        'deadlines': deadlines,
    })

def refresh_predictions(request, userid='ivan.jakovac2@gmail.com', passwd='', seasonid='1084573'):
    '''
        This view refreshes the Prediction(s) table by downloading .csv(s) from kicktipp website.
        # This is season specific function #
    '''
    # Find last round which has started.
    last_round = Match.objects.filter(match_time__lte = timezone.now()).aggregate(Max('round'))['round__max'] 
    last_round = last_round if last_round is not None else 0
    # Iterate through all rounds
    for rnd in range(1,last_round+1):
        # Initialize https session
        with requests.session() as s:
            # Get initial cookies
            response = s.get('https://www.kicktipp.com/info/profil/login')

            # Login to the website
            auth_data = {'kennung': userid, 'passwort': passwd}
            response = s.post('https://www.kicktipp.com/info/profil/loginaction', data=auth_data)

            # Download predictions
            download_data = {'tippsaisonId': seasonid, 'datenauswahl': 'tipps', 'tippspieltagIndex': rnd, 'wertung': 'einzelwertung'}
            response = s.post('https://www.kicktipp.com/trutinebezobrazne/spielleiter/datenexport', data=download_data, stream=True)

            # Handle zipped .csv file
            zip_file = zipfile.ZipFile(io.BytesIO(response.content))
            with zip_file.open(zip_file.namelist()[0], 'r') as bytesCSV:
                filedata = list(csv.reader(io.TextIOWrapper(bytesCSV, 'utf-8'),delimiter=';'))
                # extract games
                header_row = filedata[0][2:]
                fixtures = [fixture.split(' - ') for fixture in header_row]
                for row in filedata[1:]:
                    userid = row[1]
                    for index, prediction in enumerate(row[2:]):
                        if prediction!='' and prediction!='-:-':
                            HG, AG = prediction.split(':')
                            obj, created = Prediction.objects.get_or_create(
                                user=User.objects.filter(userid__exact=userid)[0],
                                match=Match.objects.filter(home_team__exact=fixtures[index][0]).filter(away_team__exact=fixtures[index][1])[0],
                                home_goals=HG,
                                away_goals=AG,
                            )

def upload_predictions(request):
    '''
        View to upload zipped .csv with kicktipp predictions.
    '''
    # if there is no file in POST, get upload form.
    if request.method == 'GET':
        form = UploadFileForm()
        return render(request, 'livescore/upload_predictions.html', {'form': form})
    
    # if there is a file in POST, process given .zip (no file format validation!)
    elif request.method == 'POST':
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = request.FILES['file']

            # Handle zipped .csv file
            zip_file = zipfile.ZipFile(uploaded_file.file)
            with zip_file.open(zip_file.namelist()[0], 'r') as bytesCSV:
                filedata = list(csv.reader(io.TextIOWrapper(bytesCSV, 'utf-8'),delimiter=';'))
                # Extract games
                header_row = filedata[0][2:]
                fixtures = [fixture.split(' - ') for fixture in header_row]
                for row in filedata[1:]:
                    userid = row[1]
                    for index, prediction in enumerate(row[2:]):
                        if prediction!='' and prediction!='-:-':
                            HG, AG = prediction.split(':')
                            obj, created = Prediction.objects.get_or_create(
                                user=User.objects.filter(userid__exact=userid)[0],
                                match=Match.objects.filter(home_team__exact=fixtures[index][0]).filter(away_team__exact=fixtures[index][1])[0],
                                home_goals=HG,
                                away_goals=AG,
                            )

    # return to the home page
    return redirect('livescores')

def refresh_dates(request):
    '''
        Scape the latest fixture to know when the Scheduled fixture is Postponed.
    '''
    # Return scheduled and postponed fixtures
    scheduled_matches = Match.objects.filter(status__in=['S', 'P'])

    # Iterate throughout all scheduled fixtures
    for match in scheduled_matches:
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

        datetime_pattern = re.compile(r'(\d+\.\d+.\d+ \d+:\d+)')
    
        divs = soup.find_all("div", attrs={'class':'detail'})
        for div in divs[:4]:
            contents = str(div.encode())
            if mobj := datetime_pattern.search(contents):
                # Div represents the fixture's date
                match.match_time = timezone.make_aware(datetime.strptime(mobj.group(1),'%d.%m.%Y %H:%M'), timezone.get_default_timezone())
        match.status = 'S'
        # Push changes to the db
        match.save()
        time.sleep(0.5)

    # redirect to the home page
    return redirect('livescores')

def refresh_status(request):
    '''
        Set all games to Scheduled to recalculate the points
        # USE ONLY WHEN THE SCORES ARE REFRESHED AUTOMATICALLY (MIDDLEWARE active) #
    '''
    Match.objects.all().update(status='S')

def refresh(request):
    '''
        The wrapper for the refresh procedures. Upon completion user is redirected to the home page
    '''
    # pass request variable to both function in case some new features need it.
    # Set to scheduled
    refresh_status(request)
    # Scrape for dates
    refresh_dates(request)
    # Scrape for scores
    refresh_predictions(request)

    # redirect to the home page
    return redirect('livescores')


#KAHR KAHR
def post_predictions(request):
    if request.method == 'POST':
        # Here, you will handle form submission and save data to the database
        # Redirect to a new URL after posting:
        return redirect('post_predictions')  # Redirect to the same page or to another success page
    
    json_file_path = 'livescore/data/EURO24/matches.json'

    # Load games data from JSON file
    with open(json_file_path, 'r') as file:
        groups = json.load(file)

    # Convert match_time strings to datetime objects
    for group in groups:
        for game in group['matches']:
            # Parse the match_time string into a datetime object
            game['match_time'] = datetime.strptime(game['match_time'], '%Y-%m-%d %H:%M:%S')

    return render(request, 'livescore/post_predictions.html', {'groups': groups})