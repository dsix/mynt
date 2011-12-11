# -*- coding: utf-8 -*-

from __future__ import unicode_literals

from argparse import ArgumentParser
from calendar import timegm
import logging
import re
from time import time

from pkg_resources import load_entry_point
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from mynt.containers import Archives, Config, Page, Post, Tags
from mynt.exceptions import ConfigException, OptionException, RendererException
from mynt.fs import Directory, File
from mynt.utils import get_logger, normpath


logger = get_logger('mynt')


class Mynt(object):
    config = {
        'assets_url': '/assets',
        'base_url': '/',
        'date_format': '%A, %B %d, %Y',
        'markup': 'markdown',
        'parser': 'misaka',
        'posts_url': '/<year>/<month>/<day>/<title>/',
        'pygmentize': True,
        'renderer': 'jinja',
        'tag_layout': None,
        'tags_url': '/'
    }
    
    _parser = None
    _renderer = None
    
    archives = []
    pages = []
    posts = []
    tags = Tags()
    
    
    def __init__(self, args = None):
        self._start = time()
        
        self.opts = self._get_opts(args)
        self.src = Directory(self.opts['src'])
        self.dest = Directory(self.opts['dest'])
        
        logger.setLevel(getattr(logging, self.opts['level'], logging.INFO))
        logger.debug('>> Initializing\n..  src:  {0}\n..  dest: {1}'.format(self.src, self.dest))
        
        if self.src == self.dest:
            raise OptionException('Source and destination must differ.')
        elif self.src.path in ('/', '//') or self.dest.path in ('/', '//'):
            raise OptionException('Root is not a valid source or destination.')
        
        logger.debug('>> Searching for config')
        
        for ext in ('.yml', '.yaml'):
            f = File(normpath(self.src.path, 'config' + ext))
            
            if f.exists:
                logger.debug('..  found: {0}'.format(f.path))
                
                try:
                    self.config.update(Config(f.content))
                except ConfigException as e:
                    raise ConfigException(e.message, 'src: {0}'.format(f.path))
                
                break
        else:
            logger.debug('..  no config file found')
        
        for opt in ('base_url',):
            if opt in self.opts:
                self.config[opt] = self.opts[opt]
        
        self.renderer.register({'site': self.config})
    
    
    def _get_opts(self, args):
        opts = {}
        
        parser = ArgumentParser(description = 'A static blog generator.')
        
        parser.add_argument('src', nargs = '?', default = '.', metavar = 'source', help = 'The location %(prog)s looks for source files.')
        parser.add_argument('dest', metavar = 'destination', help = 'The location %(prog)s outputs to.')
        
        level = parser.add_mutually_exclusive_group()
        
        level.add_argument('-l', '--level', default = b'INFO', type = str.upper, choices = ['DEBUG', 'INFO', 'WARNING', 'ERROR'], help = 'Sets %(prog)s\'s log level.')
        level.add_argument('-q', '--quiet', action = 'store_const', const = 'ERROR', dest = 'level', help = 'Sets %(prog)s\'s log level to ERROR.')
        level.add_argument('-v', '--verbose', action = 'store_const', const = 'DEBUG', dest = 'level', help = 'Sets %(prog)s\'s log level to DEBUG.')
        
        parser.add_argument('--base-url', help = 'Sets the site\'s base URL.')
        parser.add_argument('-f', '--force', action = 'store_true', help = 'Forces generation deleting the destination if it already exists.')
        
        for option, value in vars(parser.parse_args(args)).iteritems():
            if value is not None:
                if isinstance(option, str):
                    option = option.decode('utf-8')
                
                if isinstance(value, str):
                    value = value.decode('utf-8')
                
                opts[option] = value
        
        return opts
    
    def _get_parser(self):
        return load_entry_point('mynt', 'mynt.parsers.{0}'.format(self.config['markup']), self.config['parser'])
    
    def _get_path(self, url):
        parts = [self.dest.path] + url.split('/')
        
        if url.endswith('/'):
            parts.append('index.html')
        
        return normpath(*parts)
    
    def _get_post_url(self, date, slug):
        subs = {
            '<year>': '%Y',
            '<month>': '%m',
            '<day>': '%d',
            '<i_month>': str(date.month).decode('utf-8'),
            '<i_day>': str(date.day).decode('utf-8'),
            '<title>': slug
        }
        
        link = self.config['posts_url'].replace('%', '%%')
        
        for match, replace in subs.iteritems():
            link = link.replace(match, replace)
        
        return date.strftime(link)
    
    def _get_tag_url(self, name):
        end = '/' if self.config['tags_url'].endswith('/') else '.html'
        
        return '{0}/{1}{2}'.format(self.config['tags_url'], name, end)
    
    def _get_renderer(self):
        return load_entry_point('mynt', 'mynt.renderers', self.config['renderer'])
    
    def _highlight(self, match):
            language, code = match.groups()
            formatter = HtmlFormatter(linenos = 'table')
            
            for pattern, replace in [('&amp;', '&'), ('&gt;', '>'), ('&lt;', '<'), ('&quot;', '"')]:
                code = code.replace(pattern, replace)
            
            try:
                code = highlight(code, get_lexer_by_name(language), formatter)
            except ClassNotFound:
                code = highlight(code, get_lexer_by_name('text'), formatter)
            
            return '<div class="code"><div>{0}</div></div>'.format(code)
    
    def _pygmentize(self, html):
        if not self.config['pygmentize']:
            return html
        
        return re.sub(r'<pre[^>]+lang="([^>]+)"[^>]*><code>(.+?)</code></pre>', self._highlight, html, flags = re.S)
    
    
    def _parse(self):
        logger.info('>> Parsing')
        
        path = Directory(normpath(self.src.path, '_posts'))
        
        logger.debug('..  src: {0}'.format(path))
        
        for f in path:
            post = Post(f)
            
            content = self.parser.parse(self.renderer.from_string(post.bodymatter, post.frontmatter))
            excerpt = re.search(r'\A.*?(<p>.+?</p>)?', content, re.M | re.S).group(1)
            
            data = {
                'content': content,
                'date': post.date.strftime(self.config['date_format']),
                'excerpt': excerpt,
                'tags': [],
                'timestamp': timegm(post.date.utctimetuple()),
                'url': self._get_post_url(post.date, post.slug)
            }
            
            data.update(post.frontmatter)
            data['tags'].sort(key = unicode.lower)
            
            self.posts.append(data)
            
            for tag in data['tags']:
                if tag not in self.tags:
                    self.tags[tag] = []
                
                self.tags[tag].append(data)
        
        if self.posts:
            self.posts.sort(key = lambda post: post['timestamp'], reverse = True)
            
            self.archives = Archives(self.posts)
            
            sorting = []
            
            for name, posts in self.tags:
                posts.sort(key = lambda post: post['timestamp'], reverse = True)
                
                sorting.append({
                    'name': name,
                    'count': len(posts),
                    'posts': posts,
                    'url': self._get_tag_url(name)
                })
            
            sorting.sort(key = lambda tag: tag['name'].lower())
            sorting.sort(key = lambda tag: tag['count'], reverse = True)
            
            self.tags.clear()
            
            for tag in sorting:
                self.tags[tag['name']] = tag
        else:
            logger.debug('..  no posts found')
    
    def _render(self):
        self._parse()
        
        logger.info('>> Rendering')
        
        self.renderer.register({
            'posts': self.posts,
            'tags': self.tags
        })
        
        logger.debug('..  posts')
        
        for post in self.posts:
            try:
                self.pages.append(Page(
                    self._get_path(post['url']),
                    self._pygmentize(self.renderer.render(post['layout'], {'post': post}))
                ))
            except RendererException as e:
                raise RendererException(e.message, '{0} in post \'{1}\''.format(post['layout'], post['title']))
        
        logger.debug('..  pages')
        
        for f in self.src:
            if f.extension not in ('.html', '.htm', '.xml'):
                continue
            
            template = f.path.replace(self.src.path, '')
            
            self.pages.append(Page(
                normpath(self.dest.path, template),
                self._pygmentize(self.renderer.render(template))
            ))
        
        if self.config['tag_layout'] and self.tags:
            logger.debug('..  tags')
            
            for name, data in self.tags:
                self.pages.append(Page(
                    self._get_path(data['url']),
                    self._pygmentize(self.renderer.render(self.config['tag_layout'], {'tag': data}))
                ))
    
    
    def generate(self):
        self._render()
        
        logger.info('>> Generating')
        
        assets_src = Directory(normpath(self.src.path, '_assets'))
        assets_dest = Directory(normpath(self.dest.path, *self.config['assets_url'].split('/')))
        
        if self.dest.exists:
            if not self.opts['force']:
                raise OptionException('Destination already exists.', 'the -f option must be used to force generation by deleting the destination')
            
            self.dest.rm()
        
        self.dest.mk()
        
        for page in self.pages:
            page.mk()
        
        if assets_src.exists:
            for asset in assets_src:
                asset.cp(asset.path.replace(assets_src.path, assets_dest.path))
        
        logger.info('Completed in {0:.3f}s'.format(time() - self._start))
    
    
    @property
    def parser(self):
        if self._parser is None:
            self._parser = self._get_parser()(self.config.get(self.config['parser'], {}))
        
        return self._parser
    
    @property
    def renderer(self):
        if self._renderer is None:
            self._renderer = self._get_renderer()(self.src.path, self.config.get(self.config['renderer'], {}))
        
        return self._renderer