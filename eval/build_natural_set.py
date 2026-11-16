"""Builds eval/natural_set.json: NATURAL rephrasings of verified golden questions (eval/quality_set.json).

    python eval/build_natural_set.py            # writes eval/natural_set.json (read-only on the index: only the log-* items are verified live)
    python eval_natural.py                      # free retrieval-mode run + paraphrase-robustness report

Every paraphrase keeps the source item's verified expectation (expect / retr / forbid / dynamic / campus / behavior), so the check stays
mechanical: "does the context the model would see still hold the fact when the question is worded the way people really type?".
Phrasing types: casual, indirect, slang, abbrev, typo, implied (subject left out), parent, statement (no question), multi_part (two source
items in one message; expectations merged), follow_up (2 turns, with history), log (wording taken from production logs).
`LOG_ITEMS` are real questions from the logs that have no golden source item; each carries `verify` patterns that must be found in the live
index (read-only) or the item is written with verified=false and left out of scoring.
Add a paraphrase: append to PARA[<source id>]; add a log question: append to LOG_ITEMS; re-run this script.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
U, A = "user", "assistant"


def h(*turns):
    """history helper: h('question', 'answer', 'question2') -> alternating user/assistant turns"""
    return [{"role": U if i % 2 == 0 else A, "content": t} for i, t in enumerate(turns)]


# source id -> [(phrasing type, question[, history or {"with": [other source ids]}])]
PARA = {
    # ------------------------------------------------------------------ calendar
    "cal-01": [("log", "when do we get out for thanksgiving"), ("statement", "need the thanksgiving break dates"), ("parent", "Hi, what days are the kids off for Thanksgiving week?")],
    "cal-02": [("casual", "when's winter break and when are we back"), ("indirect", "how long do we get off in december")],
    "cal-03": [("casual", "when does school get out for summer"), ("slang", "whens the last day of skool")],
    "cal-04": [("casual", "what day is graduation and what time"), ("parent", "We're booking flights for my daughter's graduation, when is it?")],
    "cal-05": [("casual", "do we get mlk day off"), ("abbrev", "school on MLK day?")],
    "cal-06": [("casual", "when's spring break"), ("statement", "spring break dates pls")],
    "cal-07": [("casual", "when is february break"), ("indirect", "do we get a week off in february")],
    "cal-08": [("abbrev", "when are US PT conferences"), ("parent", "When are the next parent teacher conferences for my high schooler?")],
    "cal-09": [("casual", "what day is winter formal and what time does it start"), ("typo", "wen is the winter formel")],
    "cal-10": [("casual", "what day is prom this year"), ("statement", "prom date and time")],
    "cal-12": [("casual", "when's the upper school musical"), ("indirect", "what dates is the high school doing the musical")],
    "cal-13": [("casual", "when is intersession"), ("implied", "intersession dates?")],
    "cal-14": [("casual", "when do upper school trips happen"), ("abbrev", "US trips dates")],
    "cal-15": [("log", "when's break"), ("casual", "when is our next day off")],
    "cal-17": [("casual", "do we have school on juneteenth"), ("indirect", "is juneteenth a holiday for us")],
    "cal-18": [("casual", "do we go back on jan 4"), ("indirect", "are there classes the monday after winter break, january 4")],
    "cal-19": [("casual", "when's state of nueva"), ("parent", "What date and time is the State of Nueva event?")],
    "cal-20": [("casual", "what day is the sat at school"), ("indirect", "is nueva giving the SAT this year and when")],
    # ------------------------------------------------------------------ day types
    "day-01": [("log", "is it an a day"), ("casual", "a or b day today?")],
    "day-02": [("log", "what day is it tomorrow"), ("slang", "tmrw a day or b day")],
    "day-03": [("casual", "is nov 30 an a day or b day"), ("abbrev", "day type 11/30")],
    "day-04": [("casual", "what order are classes in on nov 5"), ("indirect", "which block comes third on thursday november 5")],
    "day-05": [("casual", "whats the difference between an a day and an a prime day"), ("indirect", "why is there an apostrophe after the A on some days")],
    "day-06": [("casual", "what classes do i have on a b day"), ("implied", "b day blocks?")],
    "day-07": [("casual", "do we have regular classes on nov 18"), ("indirect", "whats special about november 18")],
    "day-08": [("casual", "do we have school the friday after thanksgiving"), ("slang", "is there skool the day after thanksgiving")],
    "day-10": [("casual", "do we have class dec 9"), ("indirect", "whats going on december 9, is it a normal day")],
    "day-12": [("casual", "is dec 3 an a or b day"), ("statement", "need to know what kind of day december 3 is")],
    # ------------------------------------------------------------------ bell schedule
    "bell-01": [("casual", "when does block 2 start and end"), ("follow_up", "and block 2?", h("what time does block 1 start", "Block 1 starts at 8:55 AM."))],
    "bell-02": [("statement", "need the bell schedule"), ("casual", "whats the schedule for a normal day")],
    "bell-03": [("casual", "when does school start and when do we get out"), ("parent", "What are the start and dismissal times for the high school?")],
    "bell-04": [("casual", "how much time do we get for lunch"), ("indirect", "when does lunch start and end")],
    "bell-05": [("casual", "when's block 7"), ("abbrev", "b7 time")],
    "bell-06": [("casual", "when is block 4 on an a prime day"), ("indirect", "on A' days what time do I have my block 4 class")],
    "bell-07": [("casual", "how many blocks a day"), ("indirect", "do we have all 8 classes every day or just some")],
    "bell-08": [("casual", "what time does block 5 start"), ("indirect", "what time do i need to be at school on a b day for block 5")],
    "bell-09": [("log", "what time does school end today"), ("casual", "when do we get out of school")],
    # ------------------------------------------------------------------ athletics teams
    "ath-01": [("casual", "what sports do we have"), ("parent", "Which sports teams could my daughter join at Nueva?")],
    "ath-02": [("log", "what sports are there in the spring"), ("statement", "looking for spring varsity sports")],
    "ath-03": [("casual", "what sports are in the winter season"), ("implied", "winter sports?")],
    "ath-04": [("casual", "what sports are there in the fall for high school"), ("abbrev", "US fall sports")],
    "ath-05": [("casual", "do we have football"), ("indirect", "can i play football at nueva")],
    "ath-06": [("casual", "can i play baseball here"), ("slang", "do yall have softball or baseball")],
    "ath-08": [("casual", "do we have swimming"), ("indirect", "can i swim for nueva")],
    "ath-09": [("casual", "what sports are there for middle schoolers"), ("parent", "My son is in 7th grade, which sports can he play?")],
    "ath-11": [("log", "who's the AD"), ("casual", "who runs athletics")],
    "ath-12": [("casual", "who's the cross country coach"), ("abbrev", "xc coach?")],
    "ath-13": [("casual", "what league are we in for sports"), ("indirect", "which league do nueva's high school teams compete in")],
    "ath-14": [("casual", "how much pe do i have to do"), ("indirect", "do i have to take pe if i play a sport")],
    "ath-16": [("casual", "is there an athletic trainer"), ("indirect", "who do athletes see if they get hurt, is there a trainer on staff")],
    "ath-17": [("casual", "what forms do i need to play a sport"), ("parent", "What do I have to fill out before my kid can start practicing with a team?")],
    "ath-18": [("casual", "is there a girls varsity basketball team"), ("indirect", "can girls play varsity basketball here")],
    "ath-20": [("casual", "what league does girls basketball play in"), ("abbrev", "girls bball league?")],
    "cov-15": [("casual", "do we have golf"), ("indirect", "can i play golf for the school")],
    "cov-16": [("casual", "where are home basketball games"), ("indirect", "which gym do the basketball teams play in when they're home")],
    "cov-17": [("casual", "whats our mascot and what are the school colors"), ("indirect", "what are nueva's teams called and what colors do they wear")],
    "cov-18": [("casual", "what do i need to turn in before the first practice"), ("parent", "Which forms does my athlete need done before practice starts?")],
    "sp-01": [("casual", "do girls have a golf team and when is the season"), ("indirect", "what season do the girls play golf")],
    "sp-03": [("casual", "is there jv girls volleyball"), ("indirect", "does girls vball have a jv squad")],
    "sp-05": [("casual", "where does girls soccer play home games"), ("indirect", "what field are the girls soccer home games on")],
    "sp-07": [("casual", "what time is varsity boys basketball practice"), ("slang", "when do the varsity bball boys practice")],
    "sp-09": [("casual", "which sports have jv"), ("indirect", "what teams can i play jv on in high school")],
    "sp-10": [("casual", "what league is xc in"), ("indirect", "which league does our cross country team run in")],
    "sp-14": [("casual", "do girls have jv tennis"), ("indirect", "is there a junior varsity team for girls tennis")],
    "sp-16": [("casual", "who's the track coach"), ("indirect", "who is in charge of the track and field team")],
    # ------------------------------------------------------------------ games
    "gm-01": [("casual", "when's the first varsity boys basketball game"), ("slang", "when do varsity boys hoop for the first time")],
    "gm-03": [("casual", "who are varsity boys playing dec 8"), ("abbrev", "varsity boys bball opponent 12/8")],
    "gm-05": [("casual", "who does jv boys basketball play nov 17 and when"), ("implied", "jv boys game on the 17th of november, who and what time")],
    "gm-06": [("casual", "any home basketball games dec 14"), ("indirect", "is anyone playing basketball in our gym on december 14")],
    "gm-07": [("casual", "when do varsity boys play kehillah"), ("statement", "looking for the varsity boys basketball games against Kehillah")],
    "gm-08": [("casual", "what time do the girls play crystal springs dec 18"), ("abbrev", "girls bball vs crystal 12/18 time")],
    "gm-09": [("casual", "who do varsity boys play dec 1"), ("indirect", "which school is the boys varsity basketball team up against on december 1st")],
    "gm-11": [("casual", "what was the score when girls tennis played priory"), ("indirect", "how did the girls tennis match against priory end up")],
    "gm-12": [("casual", "when do the ms boys play basketball next"), ("abbrev", "next MS boys bball game")],
    "gm-17": [("casual", "when do the girls play design tech in basketball"), ("abbrev", "girls bball vs d tech dates")],
    "gm-18": [("follow_up", "and jv?", h("When is the first varsity boys basketball game?", "The first US varsity boys basketball game is at home against CSD Fremont.")),
              ("follow_up", "what about the jv team", h("when's the first varsity boys basketball game", "The varsity boys open at home against CSD Fremont."))],
    "gm-20": [("casual", "did girls volleyball win against summit shasta"), ("indirect", "how did the girls vball game vs summit shasta go")],
    "sp-06": [("casual", "what time do the girls play eastside in soccer on dec 3"), ("abbrev", "girls soccer vs eastside college prep 12/3 time")],
    "sp-12": [("casual", "when do the girls play burlingame in basketball"), ("indirect", "what day and time is girls basketball vs burlingame")],
    "sp-13": [("casual", "is the boys tennis schedule out"), ("indirect", "when does boys tennis play")],
    # ------------------------------------------------------------------ clubs
    "club-01": [("casual", "when's chess club"), ("follow_up", "when do they meet", h("is there a chess club", "Yes, Nueva has a Chess Club."))],
    "club-02": [("casual", "when is pinball club and who's the advisor"), ("statement", "pinball club meeting time and advisor")],
    "club-03": [("casual", "when's ethics bowl"), ("indirect", "what day and where does the ethics bowl team get together")],
    "club-04": [("casual", "who runs mock trial"), ("indirect", "which teachers are in charge of mock trial")],
    "club-05": [("casual", "when's model un"), ("indirect", "what day does model united nations meet and where")],
    "club-06": [("log", "how do i join robotics"), ("casual", "when's robotics")],
    "club-07": [("casual", "do we have a mahjong club"), ("indirect", "is there anywhere to play mahjong at school")],
    "club-08": [("casual", "any clubs for coding or ai"), ("indirect", "i like programming, what clubs should i look at")],
    "club-09": [("casual", "how many clubs are there"), ("indirect", "about how many clubs can upper schoolers choose from")],
    "club-10": [("casual", "do we have a climbing club"), ("indirect", "is there a club for rock climbing and when is it")],
    "club-11": [("casual", "do we have flag football"), ("indirect", "is flag football a thing here and when is it")],
    "club-13": [("casual", "what clubs are tuesday at lunch"), ("statement", "looking for clubs that meet tuesday lunch")],
    "club-15": [("casual", "how do i make a club"), ("indirect", "i want to start my own club, what do i need")],
    "club-17": [("casual", "is there robotics"), ("indirect", "does the school have a robotics team i can join")],
    # ------------------------------------------------------------------ staff / contacts
    "staff-01": [("casual", "who's the mechanical engineering teacher"), ("indirect", "which teacher has mechanical engineering this year")],
    "staff-03": [("casual", "who teaches intro to cad and when is it"), ("abbrev", "intro to CAD teacher + block")],
    "staff-04": [("casual", "when is immunology"), ("indirect", "which block do i have immunology in")],
    "staff-05": [("casual", "who's wes chao"), ("indirect", "what does mr chao teach")],
    "staff-06": [("casual", "what else does max cowan teach"), ("indirect", "what classes does mr cowan have")],
    "staff-07": [("casual", "whats the number for the san mateo front desk"), ("statement", "need the upper school front desk phone number")],
    "staff-08": [("casual", "whats the hillsborough campus phone number"), ("parent", "What number do I call to reach the Hillsborough campus office?")],
    "staff-09": [("casual", "how do i get in touch with the nurse"), ("implied", "nurse email?")],
    "staff-10": [("casual", "who's the head of school"), ("indirect", "who runs nueva")],
    "staff-11": [("casual", "who's the upper school head"), ("indirect", "who is in charge of the high school division")],
    "staff-12": [("casual", "who's the dean of students"), ("indirect", "which dean do students go to")],
    "staff-13": [("casual", "who's the strength coach"), ("indirect", "who runs strength and conditioning for athletes")],
    "staff-14": [("casual", "who's the cad teacher"), ("indirect", "who has the CAD class")],
    "cov-21": [("casual", "who's the middle school head"), ("indirect", "who is in charge of the middle school")],
    "cov-22": [("casual", "who do i ask about my tuition bill"), ("parent", "I have a question about a charge on our tuition statement, who do I contact?")],
    "cov-23": [("log", "wifi not working"), ("casual", "who do i email for tech help")],
    "cov-25": [("casual", "who's in charge of admissions"), ("indirect", "who is the admissions director at nueva")],
    "cov-26": [("casual", "who's the sophomore dean"), ("indirect", "who is the dean for 10th graders")],
    "cov-27": [("casual", "who's the registrar"), ("indirect", "who handles transcripts and course registration, the registrar?")],
    "cov-28": [("casual", "who's the counselor for upper school"), ("indirect", "who is the school counselor for high school students")],
    "cov-29": [("casual", "who do i email about the bus"), ("parent", "Where do I send questions about my child's bus route?")],
    # ------------------------------------------------------------------ policies
    "pol-01": [("casual", "can i have my phone out at school"), ("indirect", "what are the rules about phones")],
    "pol-02": [("casual", "can i use my phone at lunch"), ("indirect", "are phones ok during lunch")],
    "pol-05": [("casual", "how late is a tardy"), ("indirect", "how many minutes late until im marked tardy")],
    "pol-06": [("casual", "how many tardies make an absence"), ("indirect", "do tardies turn into absences")],
    "pol-08": [("log", "am i allowed to leave campus"), ("casual", "can 9th graders go off campus at lunch")],
    "pol-09": [("casual", "who can leave campus during the day"), ("indirect", "which grades are allowed off campus")],
    "pol-10": [("casual", "is there a dress code"), ("indirect", "what am i allowed to wear to school")],
    "pol-11": [("casual", "can i use ai for homework"), ("indirect", "is it ok to use chatgpt for my assignments")],
    "pol-12": [("casual", "what happens if i plagiarize"), ("indirect", "whats the consequence the first time someone is caught copying")],
    "pol-14": [("casual", "do we have an honor code"), ("indirect", "what are the rules about academic honesty")],
    "pol-16": [("casual", "what's the pe requirement"), ("indirect", "how much pe do upper schoolers need")],
    # ------------------------------------------------------------------ lunch
    "lun-01": [("casual", "whats for lunch nov 12 at san mateo"), ("abbrev", "SM lunch 11/12")],
    "lun-02": [("casual", "whats for lunch november 12"), ("statement", "lunch menu for nov 12 please")],
    "lun-03": [("log", "whats for lunch tmrw"), ("casual", "what are we eating tomorrow")],
    "lun-04": [("casual", "whats for lunch"), ("slang", "what's the cafe got today")],
    "lun-06": [("casual", "do they serve lunch on thanksgiving"), ("indirect", "is the cafe open thanksgiving day")],
    "lun-07": [("casual", "when is the cafe open at san mateo"), ("statement", "san mateo cafe hours")],
    "lun-09": [("casual", "is there pizza nov 12 at the middle school"), ("indirect", "does the hillsborough cafe have pizza on november 12")],
    # ------------------------------------------------------------------ events
    "ev-01": [("casual", "when's evening of the arts"), ("indirect", "what day and time is the evening of the arts show")],
    "ev-02": [("casual", "whats nueva rocks the guild"), ("indirect", "what is the guild event everyone is talking about")],
    "ev-03": [("casual", "when's the college kickoff for class of 2028 parents"), ("parent", "I have a sophomore, when is the parent college kickoff for the class of 2028?")],
    "ev-04": [("casual", "when's the us trips info night"), ("indirect", "when is the info night about upper school trips")],
    "ev-06": [("casual", "any financial aid workshops soon"), ("indirect", "when can i attend a workshop about financial assistance")],
    "ev-09": [("casual", "whats the parent fika"), ("indirect", "what is a fika and when is it")],
    "ev-10": [("casual", "when's csl day"), ("indirect", "what day is the community service learning day")],
    # ------------------------------------------------------------------ admissions / parents
    "adm-01": [("casual", "how do i sign up for a tour"), ("parent", "We'd like to come see the school, how do we set up a visit?")],
    "adm-02": [("casual", "how much does it cost to apply"), ("indirect", "is there a fee to apply")],
    "adm-03": [("log", "when are apps due"), ("casual", "when's the deadline to apply")],
    "adm-04": [("casual", "when do admissions decisions come out"), ("indirect", "when do we hear back from admissions")],
    "adm-05": [("casual", "when do we have to say yes to an offer"), ("indirect", "how long do families have to accept after getting in")],
    "adm-06": [("casual", "when's the financial aid application due"), ("abbrev", "FA app deadline")],
    "adm-07": [("casual", "where do i apply for financial aid"), ("indirect", "what website does nueva use for financial assistance")],
    "adm-08": [("casual", "how do i apply"), ("statement", "i want to apply to nueva")],
    "adm-09": [("casual", "how do i give money to the school"), ("statement", "i want to make a donation")],
    "adm-10": [("casual", "where do i get nueva merch"), ("indirect", "is there a school store for sweatshirts")],
    "adm-11": [("log", "how much is tuition"), ("casual", "how much does nueva cost"), ("indirect", "what would it cost per year to send my kid here")],
    "adm-13": [("casual", "is there aftercare"), ("parent", "Can my child stay at school after dismissal until I get off work?")],
    "adm-14": [("casual", "who gets full financial aid"), ("indirect", "what income level qualifies for full financial assistance")],
    "cov-05": [("casual", "do i have to take the ssat to apply"), ("indirect", "is an entrance test required for high school applicants")],
    "cov-06": [("casual", "how many kids get into 9th grade"), ("indirect", "how many new freshmen spots are there each year")],
    "cov-08": [("casual", "how old do you have to be for kindergarten"), ("parent", "My daughter turns five in August, can she start kindergarten?")],
    "cov-10": [("casual", "can i get the application fee waived"), ("indirect", "what if we can't afford the application fee")],
    # ------------------------------------------------------------------ campus logistics
    "camp-01": [("casual", "where's the upper school"), ("statement", "address for the high school campus")],
    "camp-02": [("casual", "where's the hillsborough campus"), ("statement", "need the hillsborough address")],
    "camp-03": [("casual", "where do middle schoolers go"), ("indirect", "is the middle school in san mateo or hillsborough")],
    "camp-05": [("casual", "i lost my water bottle where do i look"), ("indirect", "is there a lost and found")],
    "camp-06": [("casual", "how do i get a locker"), ("statement", "i need a locker")],
    "camp-07": [("casual", "can i park at school"), ("indirect", "im a senior with a car, where do i get permission to park")],
    "cov-31": [("casual", "is there a shuttle between campuses"), ("indirect", "how do i get from hillsborough to san mateo, is there a bus")],
    "cov-32": [("casual", "how many bus routes are there"), ("indirect", "how many buses does nueva run in the morning and afternoon")],
    "cov-33": [("casual", "do we get free caltrain"), ("indirect", "does the school pay for caltrain for high schoolers")],
    # ------------------------------------------------------------------ courses
    "crs-01": [("casual", "what languages do we have"), ("indirect", "which world languages are taught")],
    "crs-02": [("casual", "how much language do i need to graduate"), ("indirect", "whats the world language requirement")],
    "crs-03": [("casual", "what science do i have to take"), ("indirect", "which science classes are required to graduate")],
    "crs-04": [("casual", "what english classes do i have to take"), ("indirect", "which english courses are mandatory")],
    "crs-05": [("casual", "do we have ap classes"), ("abbrev", "any APs at nueva?")],
    "crs-08": [("casual", "are there engineering classes"), ("indirect", "what can i take if im into engineering")],
    "crs-09": [("casual", "who teaches computer vision and what block"), ("abbrev", "computer vision teacher + when")],
    "crs-10": [("casual", "when and where is software engineering"), ("indirect", "what block is software engineering and which room")],
    "crs-11": [("casual", "is there a class on consciousness"), ("indirect", "any philosophy classes about the mind and consciousness")],
    "cov-34": [("casual", "how much math do i need to graduate"), ("indirect", "whats the math requirement")],
    "cov-35": [("casual", "how many years of language do i have to take"), ("indirect", "is three years of a language required")],
    "cov-36": [("casual", "what do i need to graduate"), ("statement", "graduation requirements please")],
    "cov-37": [("casual", "what departments are there"), ("indirect", "what subject areas does the upper school have")],
    # ------------------------------------------------------------------ advisory
    "adv-01": [("casual", "who has advisory 11.1 and where"), ("abbrev", "11.1 advisors + room")],
    "adv-02": [("casual", "who are the advisors for 9.5 and what room"), ("indirect", "where does advisory 9.5 meet and with who")],
    "adv-03": [("casual", "where's intergrade group a"), ("indirect", "which room does group A go to for intergrade")],
    "adv-06": [("casual", "who has advisory 12.3"), ("indirect", "who are the advisors and room for advisory 12.3")],
    # ------------------------------------------------------------------ nexus / other
    "nx-01": [("casual", "how do i get into nexus"), ("statement", "nexus login link")],
    "nx-02": [("casual", "how do i book a tutorial"), ("indirect", "how do i get extra help time with my teacher")],
    "nx-03": [("casual", "whats a tutorial"), ("indirect", "what do people mean by tutorial here")],
    "nx-04": [("log", "what do i do if i'm sick"), ("casual", "how do i call my kid in sick")],
    "nx-05": [("casual", "how do i pick my classes for next year"), ("indirect", "where do i put in my course requests")],
    "nx-07": [("casual", "how do i get a work permit"), ("statement", "i need a work permit for my job")],
    # ------------------------------------------------------------------ traps
    "trap-05": [("casual", "do we have hockey"), ("indirect", "can i play hockey for nueva")],
    "trap-11": [("casual", "is there school on christmas"), ("indirect", "are we off on december 25")],
    "cov-12": [("casual", "is there an ice hockey team"), ("indirect", "can you play ice hockey at nueva")],
    "sp-08": [("casual", "do we have girls softball"), ("indirect", "i saw nueva softball on maxpreps, is that real")],
    # ------------------------------------------------------------------ multi-part and follow-ups built from two items
    "mp-01": [("casual", "when does school start and end and when's lunch"), ("statement", "need start time, end time and lunch time")],
    "mp-02": [("casual", "who teaches intro to cad and where"), ("follow_up", "what room is it in", h("who teaches intro to cad", "Rob Zomber teaches Intro to CAD."))],
    "mp-03": [("casual", "do we have school nov 27 and when do we go back"), ("indirect", "is nov 27 off and what day do classes start again")],
    "mp-05": [("follow_up", "what block and where", h("who teaches mechanical engineering", "Dan Norgan teaches Mechanical Engineering.")),
              ("follow_up", "when is it and what room", h("who's the mechanical engineering teacher", "Dan Norgan."))],
    "mp-07": [("follow_up", "when is it", h("what is pinball club", "Pinball Club is a student club about pinball machines.")),
              ("follow_up", "when do they meet", h("is there a pinball club", "Yes, there is a Pinball Club."))],
    "mp-09": [("follow_up", "and for middle school?", h("what sports are in the winter", "Winter Upper School sports are basketball and soccer.")),
              ("follow_up", "what about ms", h("What sports does Nueva offer in the winter?", "Boys and girls basketball and soccer."))],
    "mp-11": [("casual", "where's the upper school and whats the phone number"), ("statement", "upper school address and front desk number")],
    "mp-12": [("follow_up", "who runs it", h("when does ethics bowl meet", "Ethics Bowl meets Mondays after school.")),
              ("follow_up", "whos the leader", h("when's ethics bowl club", "Mondays after school."))],
    "mp-13": [("casual", "what time is block 3 on an a prime day"), ("indirect", "when do i have my block 3 class if its an A' day")],
    "ms-01": [("casual", "when does middle school start and get out"), ("parent", "What time is drop-off and pick-up for middle school?")],
    "ms-03": [("casual", "when's the ms musical"), ("indirect", "what dates is the middle school musical")],
    "ms-04": [("casual", "when's step up day"), ("indirect", "what day is step up day this year")],
    "ms-06": [("casual", "when's the 8th grade trip"), ("parent", "When do the eighth graders leave for their trip and when are they back?")],
    "ms-08": [("casual", "when do the 7th graders do shakespeare"), ("indirect", "what dates are the seventh grade shakespeare shows")],
    # multi-part across two source items (expectations merged)
    "cal-06": [("casual", "when's spring break"), ("statement", "spring break dates pls"), ("multi_part", "when's spring break and when's the last day of school", {"with": ["cal-03"]})],
    "staff-10": [("casual", "who's the head of school"), ("indirect", "who runs nueva"), ("multi_part", "who's the head of school and who's the upper school head", {"with": ["staff-11"]})],
    "club-01": [("casual", "when's chess club"), ("follow_up", "when do they meet", h("is there a chess club", "Yes, Nueva has a Chess Club.")),
                ("multi_part", "when do chess club and ethics bowl meet", {"with": ["club-03"]})],
    "camp-01": [("casual", "where's the upper school"), ("statement", "address for the high school campus"),
                ("multi_part", "where is the upper school and what time does it start", {"with": ["bell-03"]})],
    "adm-03": [("log", "when are apps due"), ("casual", "when's the deadline to apply"), ("multi_part", "when are applications due and how much is the fee", {"with": ["adm-02"]})],
    "staff-01": [("casual", "who's the mechanical engineering teacher"), ("indirect", "which teacher has mechanical engineering this year"),
                 ("multi_part", "who teaches mechanical engineering and who teaches computer vision", {"with": ["crs-09"]})],
}

# Real questions from the production logs that have no golden source item. Expectations are stated here and proven against the live index
# (every `verify` string must be found in a chunk; read-only). `route`: the path the question must take (canned = no retrieval, no LLM).
# `retr_forbid`: patterns that must NOT be in the context (the wrong division's games shown for an Upper School question).
LOG_ITEMS = [
    {"id": "log-01", "category": "athletics_games", "phrasing": "log", "behavior": "answer", "q": "When is boys basketball's first game",
     "expect": [], "dynamic": {"resolver": {"type": "first_event", "match": r"US Boys Basketball Varsity - Game", "kind": "game"}},
     "retr_forbid": [r"(?s)SCHOOL CALENDAR[^\n]*\n(?:- [^\n]*\n){0,2}?- [^\n]*MS \("],
     "verify": ["US Boys Basketball Varsity - Game"], "notes": "default = Upper School varsity opener; the MS games must not lead the calendar block"},
    {"id": "log-02", "category": "multi_part_follow_up", "phrasing": "follow_up", "behavior": "answer", "q": "Only varsity US boys",
     "history": h("When is boys basketball's first game", "Middle School boys basketball plays Monday; the US JV boys play Tuesday at 5:30 PM."),
     "expect": [], "dynamic": {"resolver": {"type": "first_event", "match": r"US Boys Basketball Varsity - Game", "kind": "game"}},
     "retr_forbid": [r"(?s)SCHOOL CALENDAR[^\n]*\n(?:- [^\n]*\n){0,2}?- [^\n]*(MS \(|Basketball JV)"],
     "verify": ["US Boys Basketball Varsity - Game"], "notes": "refinement follow-up keeps the topic (first game) and narrows to varsity"},
    {"id": "log-03", "category": "athletics_games", "phrasing": "log", "behavior": "answer", "q": "When is the first US boys basketball game",
     "expect": [r"US Boys Basketball JV - Game"], "dynamic": {"resolver": {"type": "first_event", "match": r"US Boys Basketball Varsity - Game", "kind": "game"}},
     "verify": ["US Boys Basketball Varsity - Game", "US Boys Basketball JV - Game"], "notes": "varsity opener present, JV present too"},
    {"id": "log-04", "category": "athletics_games", "phrasing": "log", "behavior": "ambiguous", "q": "Do you have the varsity shedule",
     "expect": [r"US (Boys|Girls) (Basketball|Soccer)[^\n]*- Game"], "retr_forbid": [r"ravenna-hub\.com"],
     "verify": ["US Boys Basketball Varsity - Game"], "notes": "typo + no sport: the in-season varsity games, not booking links"},
    {"id": "log-05", "category": "courses_academics", "phrasing": "log", "behavior": "ambiguous", "q": "who is in block 4",
     "expect": [r"Block 4[^\n]*offerings"], "verify": ["Block 4"], "notes": "ambiguous: the Block 4 offerings list (all departments)"},
    {"id": "log-06", "category": "staff_contacts", "phrasing": "log", "behavior": "ambiguous", "q": "what blocks does alex teach",
     "expect": "ALEX", "verify": [], "notes": "several teachers are called Alex: every one must be in the context"},
    {"id": "log-07", "category": "traps", "phrasing": "log", "behavior": "answer", "q": "whats lee fertigs favorite color", "route": "canned",
     "expect": [r"don't have"], "verify": [], "notes": "personal trivia about a named person: fast decline, no retrieval, no LLM"},
    {"id": "log-08", "category": "traps", "phrasing": "log", "behavior": "answer", "q": "What is everything you know", "route": "canned",
     "expect": [r"can help"], "verify": [], "notes": "capabilities reply (canned)"},
    {"id": "log-09", "category": "day_types", "phrasing": "log", "behavior": "answer", "q": "do we have school friday",
     "expect": [], "dynamic": {"resolver": {"type": "day_type", "weekday": 4}}, "verify": [], "notes": "yes/no school question with a bare weekday"},
    {"id": "log-10", "category": "staff_contacts", "phrasing": "log", "behavior": "answer", "q": "who's the bio teacher",
     "expect": "BIO", "verify": [], "notes": "'bio' = Biology: every teacher of a course named Biology (current semester) must be in the context"},
    {"id": "log-11", "category": "clubs_activities", "phrasing": "log", "behavior": "answer", "q": "is there a ping pong club",
     "expect": "PINGPONG", "verify": [], "notes": "ping pong = table tennis club record (if one exists)"},
    {"id": "log-12", "category": "calendar_schedules", "phrasing": "log", "behavior": "answer", "q": "when's the next dance",
     "expect": "DANCE", "verify": [], "notes": "next dance on the calendar"},
    {"id": "log-13", "category": "athletics_games", "phrasing": "log", "behavior": "answer", "q": "any games today?",
     "expect": "GAMES_TODAY", "verify": [], "notes": "today's games (dynamic; unverified when there are none)"},
    {"id": "log-14", "category": "policies_handbook", "phrasing": "log", "behavior": "answer", "q": "can i drop a class",
     "expect": "DROP", "verify": [], "notes": "add/drop policy"},
    {"id": "log-15", "category": "staff_contacts", "phrasing": "log", "behavior": "answer", "q": "who do i talk to about switching classes",
     "expect": "SWITCH", "verify": [], "notes": "schedule change contact"},
]


def _live_expectations(sb, today):
    """Expectations for the log items that depend on what the index holds today. -> {token: (expect list | None, verify note)}"""
    import re
    from datetime import timedelta
    T = "document_chunks_v2"
    out = {}
    ents = sb.table(T).select("content,metadata").eq("metadata->>source", "school_courses").in_("metadata->>kind", ["teacher", "course"]).limit(1000).execute().data or []
    ents = [r for r in ents if not (r.get("metadata") or {}).get("superseded")]
    alex = sorted({(r["metadata"].get("teacher") or "") for r in ents if r["metadata"].get("kind") == "teacher" and (r["metadata"].get("teacher") or "").lower().startswith("alex ")})
    out["ALEX"] = ([re.escape(n) for n in alex] if len(alex) >= 2 else None, f"teachers named Alex: {alex}")
    fall = today.month >= 8
    bio = set()
    for r in ents:
        m = r["metadata"]
        if m.get("kind") == "course" and re.fullmatch(r"biology", (m.get("course") or "").strip(), re.I) and (("Fall" in (m.get("semester") or "")) == fall):
            bio.update(re.findall(r"(?:Block \d[^:]*: )([A-Z][a-z]+ [A-Z][\w'-]+)", r["content"]))
    out["BIO"] = (["|".join(re.escape(n) for n in sorted(bio))] if bio else None, f"Biology teachers: {sorted(bio)}")
    clubs = sb.table(T).select("content,metadata").eq("metadata->>source", "school_clubs").limit(400).execute().data or []
    pp = [r["metadata"].get("club") for r in clubs if re.search(r"ping.?pong|table tennis", r["content"], re.I) and r["metadata"].get("kind") == "club"]
    out["PINGPONG"] = ([re.escape(pp[0])] if pp else None, f"ping pong clubs: {pp}")
    ev = sb.table(T).select("metadata").eq("metadata->>source", "calendar").gte("metadata->>event_date", today.isoformat()).limit(1000).execute().data or []
    dances = sorted(((r["metadata"].get("event_date"), r["metadata"].get("summary")) for r in ev if re.search(r"\b(dance|formal|prom)\b", r["metadata"].get("summary") or "", re.I)
                     and not re.search(r"dance (class|concert|show|performance|rehears)|formal (attire|dress)", r["metadata"].get("summary") or "", re.I)))
    out["DANCE"] = ([re.escape(dances[0][1][:24])] if dances else None, f"next dances: {dances[:3]}")
    gm = sb.table(T).select("metadata").in_("metadata->>source", ["calendar", "calendar_sheet"]).eq("metadata->>event_date", today.isoformat()).eq("metadata->>kind", "game").limit(50).execute().data or []
    out["GAMES_TODAY"] = (None, f"games today: {len(gm)} (dynamic by day; not scored)")
    for tok, rx in (("DROP", r"(add/drop|drop a (class|course)|dropping a (class|course)|add or drop)"), ("SWITCH", r"(schedule change|change (a|your|their) (class|course|schedule)|add/drop)")):
        rows = sb.table(T).select("chunk_id,content,metadata").or_("content.ilike.%add/drop%,content.ilike.%drop a c%,content.ilike.%schedule change%").limit(40).execute().data or []
        rows = [r for r in rows if not (r.get("metadata") or {}).get("superseded") and re.search(rx, r["content"], re.I)]
        out[tok] = ([rx] if rows else None, f"{len(rows)} live chunks match {rx}")
    return out


def build(verify_live: bool = True) -> list[dict]:
    src = {i["id"]: i for i in json.load(open(HERE / "quality_set.json"))}
    out = []
    for sid, paras in PARA.items():
        s = src[sid]
        if not s.get("verified"):
            raise SystemExit(f"{sid} is not a verified source item")
        for n, p in enumerate(paras, 1):
            kind, q = p[0], p[1]
            extra = p[2] if len(p) > 2 else None
            item = {k: s[k] for k in ("category", "persona", "campus", "behavior", "forbid", "dynamic", "verified", "retr")}
            item.update(id=f"{sid}~{n}", source_id=sid, phrasing=kind, q=q, history=[], expect=list(s["expect"]), direct_q=s["q"])
            if isinstance(extra, list):
                item["history"] = extra
            elif isinstance(extra, dict):
                for oid in extra["with"]:
                    o = src[oid]
                    if not o.get("verified") or o.get("dynamic") or s.get("dynamic"):
                        raise SystemExit(f"{sid}+{oid}: multi-part needs two verified, non-dynamic items")
                    item["expect"] = item["expect"] + list(o["expect"])
                    item["retr"] = (list(s["retr"] or s["expect"]) + list(o["retr"] or o["expect"])) if (s.get("retr") or o.get("retr")) else None
                    item["forbid"] = list(s["forbid"]) + list(o["forbid"])
                    item["with"] = extra["with"]
            out.append(item)
    live = {}
    if verify_live:
        os.environ.setdefault("ANALYTICS_DISABLED", "1")
        os.environ.setdefault("WARMUP_DISABLED", "1")
        os.environ.setdefault("ANSWER_L2_DISABLED", "1")
        sys.path.insert(0, str(ROOT))
        import query_server as qs
        from datetime import datetime
        sb = qs.get_supabase()
        today = datetime.now(qs.PACIFIC).date()
        live = _live_expectations(sb, today)
    for li in LOG_ITEMS:
        item = {"persona": "us_student", "campus": None, "forbid": [], "dynamic": None, "retr": None, "history": [], "source_id": None, "direct_q": None, **li}
        note = ""
        if isinstance(item["expect"], str):
            exp, note = live.get(item["expect"], (None, "not verified (no live check run)"))
            item["expect"] = exp or []
            item["verified"] = bool(exp)
        else:
            ok = True
            if verify_live and item["verify"]:
                for v in item["verify"]:
                    n = sb.table("document_chunks_v2").select("chunk_id", count="exact").ilike("content", f"%{v}%").limit(1).execute().count or 0
                    ok = ok and n > 0
                    note += f"{v!r}: {n} chunks; "
            item["verified"] = ok and (verify_live or not item["verify"])
        if item["dynamic"]:
            item["dynamic"] = {"as_of": "2026-11-15", "valid_through": "2027-06-11", "note": "", **item["dynamic"]}
        item["verification"] = note.strip() or "deterministic reply (no index fact needed)"
        out.append(item)
    return out


if __name__ == "__main__":
    items = build(verify_live="--offline" not in sys.argv)
    json.dump(items, open(HERE / "natural_set.json", "w"), indent=1, ensure_ascii=False)
    import collections
    ver = [i for i in items if i["verified"]]
    print(f"wrote {HERE / 'natural_set.json'}: {len(items)} items ({len(ver)} verified), {len({i['source_id'] for i in items if i['source_id']})} source items")
    print("by phrasing:", dict(collections.Counter(i["phrasing"] for i in ver)))
    print("by category:", dict(collections.Counter(i["category"] for i in ver)))
    for i in items:
        if not i["source_id"]:
            print(f"  {i['id']} verified={i['verified']}  {i['verification'][:150]}")
