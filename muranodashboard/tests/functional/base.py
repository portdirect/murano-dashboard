#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import json
import logging
import os
import six.moves.urllib.parse as urlparse
import sys
import testtools
import time
import uuid

from glanceclient import client as gclient
from keystoneauth1.identity import v3
from keystoneauth1 import session as ks_session
from keystoneclient.v3 import client as ks_client
from muranoclient.glance import client as glare_client
import muranoclient.v1.client as mclient
from oslo_log import handlers
from oslo_log import log
from selenium.common import exceptions as exc
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
import selenium.webdriver.common.by as by
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support import ui

import config.config as cfg
from muranodashboard.tests.functional import consts
from muranodashboard.tests.functional import utils

logger = log.getLogger(__name__).logger
logger.level = logging.DEBUG
logger.addHandler(handlers.ColorHandler())

if sys.version_info >= (2, 7):
    class BaseDeps(testtools.TestCase):
        pass
else:
    # Define asserts for python26
    import unittest2

    class BaseDeps(testtools.TestCase,
                   unittest2.TestCase):
        pass


class UITestCase(BaseDeps):
    @classmethod
    def setUpClass(cls):
        auth = v3.Password(user_domain_name='Default',
                           username=cfg.common.user,
                           password=cfg.common.password,
                           project_domain_name='Default',
                           project_name=cfg.common.tenant,
                           auth_url=cfg.common.keystone_url)
        session = ks_session.Session(auth=auth)
        cls.keystone_client = ks_client.Client(session=session)
        cls.auth_ref = auth.get_auth_ref(session)
        cls.service_catalog = cls.auth_ref.service_catalog
        if utils.glare_enabled():
            glare_endpoint = "http://127.0.0.1:9494"
            artifacts_client = glare_client.Client(
                endpoint=glare_endpoint, token=cls.auth_ref.auth_token,
                insecure=False, key_file=None, ca_file=None, cert_file=None,
                type_name="murano", type_version=1)
        else:
            artifacts_client = None
        cls.murano_client = mclient.Client(
            artifacts_client=artifacts_client,
            endpoint_override=cfg.common.murano_url,
            session=session)

        cls.url_prefix = urlparse.urlparse(cfg.common.horizon_url).path or ''
        if cls.url_prefix.endswith('/'):
            cls.url_prefix = cls.url_prefix[:-1]

    def setUp(self):
        super(UITestCase, self).setUp()

        self.driver = webdriver.Firefox()
        self.addCleanup(self.driver.quit)
        self.driver.maximize_window()
        self.driver.get(cfg.common.horizon_url + '/murano/environments')
        self.driver.implicitly_wait(30)
        self.addOnException(self.take_screenshot)
        self.log_in()
        self.projects_to_delete = []
        self.switch_to_project(cfg.common.tenant)

    def tearDown(self):
        super(UITestCase, self).tearDown()

        self.switch_to_project(cfg.common.tenant)

        for project_id in self.projects_to_delete:
            self.keystone_client.projects.delete(project_id)

        for env in self.murano_client.environments.list():
            self.remove_environment(env.id)

    def gen_random_resource_name(self, prefix=None, reduce_by=None):
        random_name = str(uuid.uuid4()).replace('-', '')[::reduce_by]
        if prefix:
            random_name = prefix + '_' + random_name
        return random_name

    def remove_environment(self, environment_id, timeout=180):
        self.murano_client.environments.delete(environment_id)

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                self.murano_client.environments.get(environment_id)
                time.sleep(1)
            except Exception:
                # TODO(smurashov): bug/1378764 replace Exception to NotFound
                return
        raise Exception(
            'Environment {0} was not deleted in {1} seconds'.format(
                environment_id, timeout))

    @classmethod
    def create_user(cls, name, password=None, email=None, tenant_id=None):
        if tenant_id is None:
            projects = cls.keystone_client.projects.list()
            tenant_id = [project.id for project in projects
                         if project.name == cfg.common.tenant][0]
            cls.keystone_client.users.create(name, domain='default',
                                             password=password,
                                             email=email,
                                             project=tenant_id,
                                             enabled=True)
        else:
            cls.keystone_client.users.create(name, domain='default',
                                             password=password,
                                             email=email,
                                             project=tenant_id,
                                             enabled=True)
        roles = cls.keystone_client.roles.list()
        role_id = [role.id for role in roles if role.name == 'Member'][0]
        users = cls.keystone_client.users.list()
        user_id = [user.id for user in users if user.name == name][0]
        cls.keystone_client.roles.grant(role_id, user=user_id,
                                        project=tenant_id)

    @classmethod
    def delete_user(cls, name):
        cls.keystone_client.users.find(name=name).delete()

    def get_tenantid_by_name(self, name):
        """Returns TenantID of the project by project's name"""
        tenant_id = [tenant.id for tenant
                     in self.keystone_client.projects.list()
                     if tenant.name == name]
        return tenant_id[0]

    def create_project(self, name):
        project = self.keystone_client.projects.create(
            name=name, domain="default", description="For Test Purposes",
            enabled=True)
        self.projects_to_delete.append(project.id)
        return project.id

    def add_user_to_project(self, project_id, user_id, user_role=None):
        if not user_role:
            roles = self.keystone_client.roles.list()
            role_id = [role.id for role in roles if role.name == 'Member'][0]
        if not user_id:
            user_name = cfg.common.user
            users = self.keystone_client.users.list()
            user_id = [user.id for user in users if user.name == user_name][0]
        self.keystone_client.roles.grant(role_id, user=user_id,
                                         project=project_id)

    def switch_to_project(self, name):
        projects_xpath = ("//ul[contains(@class, navbar-nav)]"
                          "//li[contains(@class, dropdown)]")
        name_xpath = ("//a//span[contains(@class, dropdown-title)"
                      "and normalize-space(text())='{0}']".format(name))
        btn_xpath = "//a[contains(@class, dropdown-toggle) and @href='#']"
        projects_list = self.driver.find_element_by_xpath(projects_xpath)
        if projects_list.text != name:
            if 'open' not in projects_list.get_attribute('class'):
                projects_list.find_element_by_xpath(btn_xpath).click()
            projects_list.find_element_by_xpath(name_xpath).click()
            self.wait_for_alert_message()
        # else the project is already set

    def take_screenshot(self, exception):
        """Taking screenshot on error

        This decorators will take a screenshot of the browser
        when the test failed or when exception raised on the test.
        Screenshot will be saved as PNG inside screenshots folder.

        """
        name = self._testMethodName
        logger.error('{0} failed'.format(name))
        screenshot_dir = './screenshots'
        if not os.path.exists(screenshot_dir):
            os.makedirs(screenshot_dir)
        filename = os.path.join(screenshot_dir, name + '.png')
        self.driver.get_screenshot_as_file(filename)

    def log_in(self, username=None, password=None):
        username = username or cfg.common.user
        password = password or cfg.common.password
        self.fill_field(by.By.ID, 'id_username', username)
        self.fill_field(by.By.ID, 'id_password', password)
        self.driver.find_element_by_xpath("//button[@type='submit']").click()
        murano = self.driver.find_element_by_xpath(consts.Applications)
        if 'collapsed' in murano.get_attribute('class'):
            murano.click()

    def log_out(self):
        user_menu = self.driver.find_element(
            by.By.XPATH, "//ul[contains(@class, 'navbar-right')]")
        user_menu.find_element(
            by.By.XPATH, ".//span[@class='user-name']").click()
        user_menu.find_element(by.By.LINK_TEXT, 'Sign Out').click()

    def fill_field(self, by_find, field, value):
        self.driver.find_element(by=by_find, value=field).clear()
        self.driver.find_element(by=by_find, value=field).send_keys(value)

    def get_element_id(self, el_name, sec=10):
        el = ui.WebDriverWait(self.driver, sec).until(
            EC.presence_of_element_located(
                (by.By.XPATH, consts.AppPackages.format(el_name))))
        path = el.get_attribute("id")
        return path.split('__')[-1]

    def select_and_click_action_for_app(self, action, app):
        self.driver.find_element_by_xpath(
            "//*[@href='{0}/murano/catalog/{1}/{2}']".format(self.url_prefix,
                                                             action,
                                                             app)).click()

    def go_to_submenu(self, link):
        element = self.wait_element_is_clickable(by.By.PARTIAL_LINK_TEXT, link)
        element.click()
        self.wait_for_sidebar_is_loaded()

    def check_panel_is_present(self, panel_name):
        self.assertIn(panel_name,
                      self.driver.find_element_by_xpath(
                          ".//*[@class='page-header']").text)

    def navigate_to(self, menu):
        el = self.wait_element_is_clickable(
            by.By.XPATH, getattr(consts, menu))
        if 'collapsed' in el.get_attribute('class'):
            el.click()
        self.wait_for_sidebar_is_loaded()

    def select_from_list(self, list_name, value, sec=10):
        locator = (by.By.XPATH,
                   "//select[contains(@name, '{0}')]"
                   "/option[@value='{1}']".format(list_name, value))
        el = ui.WebDriverWait(self.driver, sec).until(
            EC.presence_of_element_located(locator))
        el.click()

    def check_element_on_page(self, method, value, sec=10):
        try:
            ui.WebDriverWait(self.driver, sec).until(
                EC.presence_of_element_located((method, value)))
        except exc.TimeoutException:
            self.fail("Element {0} is not preset on the page".format(value))

    def check_element_not_on_page(self, method, value):
        self.driver.implicitly_wait(3)
        present = True
        try:
            self.driver.find_element(method, value)
        except (exc.NoSuchElementException, exc.ElementNotVisibleException):
            present = False
        self.assertFalse(present, u"Element {0} is preset on the page"
                                  " while it should't".format(value))
        self.driver.implicitly_wait(30)

    def check_alert_message(self, message, sec=10):
        locator = (by.By.CSS_SELECTOR, 'div.alert-dismissable')
        try:
            ui.WebDriverWait(self.driver, sec).until(
                EC.presence_of_element_located(locator))
        except exc.TimeoutException:
            self.fail("Alert is not preset on the page")

        self.assertIn(message, self.driver.find_element(*locator).text)

    def create_environment(self, env_name, by_id=False):
        if by_id:
            self.driver.find_element_by_id(
                'environments__action_CreateEnvironment').click()
        else:
            self.driver.find_element_by_css_selector(
                consts.CreateEnvironment).click()
        self.fill_field(by.By.ID, 'id_name', env_name)
        self.driver.find_element_by_id(consts.ConfirmCreateEnvironment).click()
        self.wait_for_alert_message()

    def delete_environment(self, env_name):
        self.select_action_for_environment(env_name, 'delete')
        self.driver.find_element_by_xpath(consts.ConfirmDeletion).click()
        self.wait_for_alert_message()

    def edit_environment(self, old_name, new_name):
        el_td = self.driver.find_element_by_css_selector(
            'tr[data-display="{0}"] '.format(old_name) +
            'td[data-cell-name="name"]')
        el_pencil = el_td.find_element_by_css_selector(
            'button.ajax-inline-edit')

        # hover to make pencil visible
        hover = ActionChains(self.driver).move_to_element(el_td)
        hover.perform()
        el_pencil.click()

        # fill in inline input
        el_inline_input = self.driver.find_element_by_css_selector(
            'tr[data-display="{0}"] '.format(old_name) +
            'td[data-cell-name="name"] .inline-edit-form input')
        el_inline_input.clear()
        el_inline_input.send_keys(new_name)

        # click submit
        el_submit = self.driver.find_element_by_css_selector(
            'tr[data-display="{0}"] '.format(old_name) +
            'td[data-cell-name="name"] .inline-edit-actions' +
            ' button[type="submit"]')
        el_submit.click()
        # there is no alert message

    def select_action_for_environment(self, env_name, action):
        element_id = self.get_element_id(env_name)
        more_button = consts.More.format('environments', element_id)
        self.driver.find_element_by_xpath(more_button).click()
        btn_id = "environments__row_{0}__action_{1}".format(element_id, action)
        self.driver.find_element_by_id(btn_id).click()

    def wait_for_alert_message(self, sec=5):
        locator = (by.By.CSS_SELECTOR, 'div.alert-success')
        logger.debug("Waiting for a success message")
        ui.WebDriverWait(self.driver, sec).until(
            EC.presence_of_element_located(locator))

    def wait_for_error_message(self, sec=20):
        locator = (by.By.CSS_SELECTOR, 'div.alert-danger > p')
        logger.debug("Waiting for an error message")
        ui.WebDriverWait(self.driver, sec, 1).until(
            EC.presence_of_element_located(locator))
        return self.driver.find_element(*locator).text

    def wait_element_is_clickable(self, method, element, sec=10):
        return ui.WebDriverWait(self.driver, sec).until(
            EC.element_to_be_clickable((method, element)))

    def wait_for_sidebar_is_loaded(self, sec=10):
        ui.WebDriverWait(self.driver, sec).until(
            EC.presence_of_element_located(
                (by.By.CSS_SELECTOR, "div#sidebar li.active")))
        time.sleep(0.5)


class PackageBase(UITestCase):
    @classmethod
    def setUpClass(cls):
        super(PackageBase, cls).setUpClass()
        cls.mockapp_id = utils.upload_app_package(
            cls.murano_client,
            "MockApp",
            {"categories": ["Web"], "tags": ["tag"]})
        cls.postgre_id = utils.upload_app_package(
            cls.murano_client,
            "PostgreSQL",
            {"categories": ["Databases"], "tags": ["tag"]})
        cls.hot_app_id = utils.upload_app_package(
            cls.murano_client,
            "HotExample",
            {"tags": ["hot"]}, hot=True)
        cls.deployingapp_id = utils.upload_app_package(
            cls.murano_client,
            "DeployingApp",
            {"categories": ["Web"], "tags": ["tag"]},
            hot=False,
            package_dir=consts.DeployingPackageDir)

    @classmethod
    def tearDownClass(cls):
        super(PackageBase, cls).tearDownClass()
        cls.murano_client.packages.delete(cls.mockapp_id)
        cls.murano_client.packages.delete(cls.postgre_id)
        cls.murano_client.packages.delete(cls.hot_app_id)
        cls.murano_client.packages.delete(cls.deployingapp_id)


class ImageTestCase(PackageBase):
    @classmethod
    def setUpClass(cls):
        super(ImageTestCase, cls).setUpClass()
        glance_endpoint = cls.service_catalog.url_for(service_type='image')
        cls.glance = gclient.Client('1', endpoint=glance_endpoint,
                                    session=cls.keystone_client.session)
        cls.image_title = 'New Image ' + str(time.time())
        cls.image = cls.upload_image(cls.image_title)

    @classmethod
    def tearDownClass(cls):
        super(ImageTestCase, cls).tearDownClass()
        cls.glance.images.delete(cls.image.id)

    @classmethod
    def upload_image(cls, title):
        try:
            property = {'murano_image_info': json.dumps({'title': title,
                                                         'type': 'linux'})}

            image = cls.glance.images.create(name='TestImage',
                                             disk_format='qcow2',
                                             size=0,
                                             is_public=True,
                                             properties=property)
        except Exception as e:
            logger.error("Unable to create or update image in Glance")
            raise e
        return image

    def select_and_click_element(self, element):
        self.driver.find_element_by_xpath(
            ".//*[@value = '{0}']".format(element)).click()

    def repair_image(self):
        self.driver.find_element_by_id(
            'marked_images__action_mark_image').click()
        self.select_from_list('image', self.image.id)
        self.fill_field(by.By.ID, 'id_title', self.image_title)
        self.select_from_list('type', 'linux')
        self.select_and_click_element('Mark Image')
        self.check_element_on_page(by.By.XPATH,
                                   consts.TestImage.format(self.image_title))


class FieldsTestCase(PackageBase):
    def check_error_message_is_present(self, error_message):
        self.driver.find_element_by_xpath(consts.ButtonSubmit).click()
        self.driver.find_element_by_xpath(
            consts.ErrorMessage.format(error_message))

    def check_error_message_is_absent(self, error_message):
        self.driver.find_element_by_xpath(consts.ButtonSubmit).click()

        self.driver.implicitly_wait(2)
        try:
            self.driver.find_element_by_xpath(
                consts.ErrorMessage.format(error_message))
        except (exc.NoSuchElementException, exc.ElementNotVisibleException):
            logger.info("Message {0} is not"
                        " present on the page".format(error_message))

        self.driver.implicitly_wait(30)


class ApplicationTestCase(ImageTestCase):
    def delete_component(self, component_name):
        component_id = self.get_element_id(component_name)
        self.driver.find_element_by_id(
            'services__row_{0}__action_delete'.format(component_id)).click()
        el = self.wait_element_is_clickable(by.By.LINK_TEXT,
                                            'Delete Component')
        el.click()
        self.wait_for_alert_message()

    def select_action_for_package(self, package_id, action, sec=10):
        if action == 'more':
            el = self.wait_element_is_clickable(
                by.By.XPATH, "//tr[@data-object-id='{0}']"
                             "//a[@data-toggle='dropdown']".format(package_id))
            el.click()
            ui.WebDriverWait(self.driver, sec).until(lambda s: s.find_element(
                by.By.XPATH,
                ".//*[@id='packages__row_{0}__action_download_package']".
                format(package_id)).is_displayed())
        else:
            self.driver.find_element_by_xpath(
                ".//*[@id='packages__row_{0}__action_{1}']".
                format(package_id, action)).click()

    def check_package_parameter(self, selector, column, value):
        columns = {'Tenant Name': 3, 'Active': 4, 'Public': 5}
        column_num = str(columns[column])
        column_element = self.driver.find_element_by_xpath(
            "//tr[{0}]/td[{1}]".format(selector, column_num))
        self.assertTrue(column_element.text == value,
                        "'{0}' column doesn't contain '{1}'".format(column,
                                                                    value))

    def check_package_parameter_by_id(self, package_id, column, value):
        selector = '@data-object-id="{0}"'.format(package_id)
        self.check_package_parameter(selector, column, value)

    def check_package_parameter_by_name(self, package_name, column, value):
        selector = '@data-display="{0}"'.format(package_name)
        self.check_package_parameter(selector, column, value)

    def modify_package(self, param, value):
        self.fill_field(by.By.ID, 'id_{0}'.format(param), value)
        self.driver.find_element_by_xpath(consts.InputSubmit).click()
        self.wait_for_alert_message()

    def add_app_to_env(self, app_id, app_name='TestApp', env_id=None):
        self.go_to_submenu('Browse')
        if env_id:
            action = 'add'
            app = '{0}/{1}'.format(app_id, env_id)
        else:
            action = 'quick-add'
            app = app_id
        self.select_and_click_action_for_app(action, app)
        field_id = "{0}_0-name".format(app_id)
        self.fill_field(by.By.ID, field_id, value=app_name)
        self.driver.find_element_by_xpath(consts.ButtonSubmit).click()
        self.driver.find_element_by_xpath(consts.InputSubmit).click()
        self.select_from_list('osImage', self.image.id)

        self.driver.find_element_by_xpath(consts.InputSubmit).click()
        if env_id:
            self.driver.find_element_by_xpath(consts.InputSubmit).click()
            self.wait_element_is_clickable(by.By.ID, consts.AddComponent)
            self.check_element_on_page(by.By.LINK_TEXT, app_name)
        else:
            self.wait_for_alert_message()


class PackageTestCase(ApplicationTestCase):
    @classmethod
    def setUpClass(cls):
        super(ApplicationTestCase, cls).setUpClass()
        cls.archive_name = "ToUpload"
        cls.alt_archive_name = "ModifiedAfterUpload"
        cls.manifest = os.path.join(consts.PackageDir, 'manifest.yaml')
        cls.archive = utils.compose_package(cls.archive_name, cls.manifest,
                                            consts.PackageDir)

    def tearDown(self):
        super(PackageTestCase, self).tearDown()

        for package in self.murano_client.packages.list(include_disabled=True):
            if package.name in [self.archive_name, self.alt_archive_name]:
                self.murano_client.packages.delete(package.id)

    @classmethod
    def tearDownClass(cls):
        super(ApplicationTestCase, cls).tearDownClass()
        if os.path.exists(cls.manifest):
            os.remove(cls.manifest)
        if os.path.exists(cls.archive):
            os.remove(cls.archive)
