#!/usr/bin/env python
#
# Created by: Simon Brunet
#
# Abstract:
#   Main
#
###############################################################################

import os, sys
# Setup the env
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'Utilities')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'Application')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'Db')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'Cache')))

import webapp2
import jinja2
import re
import json
import datetime

import Hashing
import Db
import Memcache
import Viewer

import logging

jinjaEnv = jinja2.Environment(autoescape=True,
    loader=jinja2.FileSystemLoader(os.path.join(os.path.dirname(__file__), 'templates'))) 

# Memcache instance
memcache = Memcache.Memcache()
# Need to flush memcache if changes are made because app engine
# reload the script which init the viewers :(
#   ... TODO brunets 2013-06-22 Fix that
memcache.Flush()

# Viewers
hitTimeStamp = Viewer.TimestampViewer()
hitCounter = Viewer.IncrementalCounterViewer()

# Attach viewers to Memcache
memcache.attach(hitTimeStamp)
memcache.attach(hitCounter)

FRONT_KEY = 'top'

def GetFrontBlog(update = False):
    key = FRONT_KEY
    query = Db.Query("Entry").order('-created').fetch
    return memcache.Get(key, query, update, 10)

def GetBlogEntry(key, update = False):
    query = Db.Model("Entry").get_by_id
    return memcache.Get(key, query, update, int(key))

class Handler(webapp2.RequestHandler):
    def Write(self, *args, **kwargs):
        self.response.out.write(*args, **kwargs)
        
    def WriteJson(self, data):
        self.response.headers['Content-Type'] = 'application/json; charset=UTF-8'
        self.Write(json.dumps(data))

    def RenderStr(self, template, **params):
        t = jinjaEnv.get_template(template)
        return t.render(params)
    
    def Render(self, template, **kwargs):
        self.Write(self.RenderStr(template, **kwargs))
        
class MainHandler(Handler):
    def get(self, json=None):
        blogs = GetFrontBlog()
        if json:
            self.WriteJson([blog.GetDict() for blog in blogs])
            return
        elapsedTime = datetime.datetime.now() - hitTimeStamp.GetTimeStamp(FRONT_KEY)
        self.Render("main.html", blogs=blogs, lastHit = elapsedTime.total_seconds())

class EntryHandler(Handler):
    def get(self, blogId, json=None):
        entry = GetBlogEntry(blogId)
        if entry:
            if json:
                self.WriteJson(entry.GetDict())
                return
            elapsedTime = datetime.datetime.now() - hitTimeStamp.GetTimeStamp(blogId)
            entry._render_text = entry.txt.replace('\n', '<br>')
            self.Render("entry.html", blog=entry, lastHit = elapsedTime.total_seconds())
        else:
            self.error(404)

class NewHandler(Handler):
    def get(self):
        self.Render("newpost.html")

    def post(self):
        title = self.request.get("subject")
        txt = self.request.get("content")

        if title and txt:
            entry = Db.Put("Entry", title = title, txt = txt)
            key = str(entry.key().id())
            GetBlogEntry(key, True)
            GetFrontBlog(True)
            self.redirect('/' + key)
        else:
            error = "Make sure both fields are not empty"
            self.Render("newpost.html", title=title, txt=txt, error=error)

class SignupHandler(Handler):

    USER_RE = re.compile(r"^[a-zA-Z0-9_-]{3,20}$")
    PASS_RE = re.compile(r"^.{3,20}$")
    EMAIL_RE = re.compile(r"^[\S]+@[\S]+\.[\S]+$")

    def renderSignup(self, **kwargs):
        self.Render("signup.html", **kwargs)

    def get(self):
        self.renderSignup()

    def post(self):
        user = str(self.request.get("username"))
        password = str(self.request.get("password"))
        verify = str(self.request.get("verify"))
        email = str(self.request.get("email"))
        
        if not user or not self.USER_RE.match(user):
            self.renderSignup(errorUser = "That's not a valid username.", user=user)
            return 
        
        if not password or not self.PASS_RE.match(password):
            self.renderSignup(errorPass = "That wasn't a valid password.", user=user)
            return
        
        if not verify or password != verify:
            self.renderSignup(errorVerify = "Your passwords didn't match.", user=user)
            return
        
        if email and not self.EMAIL_RE.match(email):
            self.renderSignup(errorEmail = "That's not a valid email.", email=email, user=user)
            return

        if Db.Query("User").filter('user =',user).get():
            self.renderSignup(errorUser = "User already choosen.", email=email, user=user)
            return

        Db.Put("User", user=user, password=Hashing.GetPwHash(password), email=email)

        self.response.headers['Content-Type'] = 'text/plain'
        self.response.headers.add_header('Set-Cookie', 'user=%s' % Hashing.GetHash(str(user)))
        
        self.redirect('/welcome')

class WelcomeHandler(Handler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        user = self.request.cookies.get('user', None)
        if user and Hashing.ValidHash(user):
            self.Write("Welcome %s!" % user.split('|')[1])
        else:
            self.redirect('/login')

class LoginHandler(Handler):
    def get(self):
        self.Render("login.html")

    def post(self):
        user = str(self.request.get("username"))
        password = self.request.get("password")

        userData = Db.Query("User").filter('user =',user).get()
        if not userData:
            self.Render("login.html", error = 'This user does not exists', user=user)
            return

        if not Hashing.ValidPw(password, userData.password):
            self.Render("login.html", error = 'Incorrect Password', user=user)
            return

        self.response.headers['Content-Type'] = 'text/plain'
        self.response.headers.add_header('Set-Cookie', 'user=%s' % Hashing.GetHash(user))
        
        self.redirect('/welcome')

class LogoutHandler(Handler):
    def get(self):
        self.response.delete_cookie("user")
        self.redirect('/signup')
        

class FlushHandler(Handler):
    def get(self):
        Memcache.Flush()
        self.redirect('/')

app = webapp2.WSGIApplication([ ('/?(.json)?', MainHandler),
                                ('/newpost', NewHandler),
                                (r'/(\d+)(.json)?', EntryHandler),
                                ('/signup', SignupHandler),
                                ('/welcome', WelcomeHandler),
                                ('/login', LoginHandler),
                                ('/logout', LogoutHandler),
                                ('/flush', FlushHandler)
                              ], debug=True)
