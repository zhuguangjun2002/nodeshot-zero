from django.http import Http404
from django.contrib.auth import login, logout
from django.utils.http import base36_to_int
from django.utils.translation import ugettext_lazy as _

from rest_framework import generics
from rest_framework import exceptions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.authentication import TokenAuthentication, SessionAuthentication
from rest_framework.permissions import IsAuthenticatedOrReadOnly, IsAuthenticated

from .models import Profile, PasswordReset, SocialLink
from .serializers import *  # noqa
from .permissions import IsProfileOwner, IsNotAuthenticated
from .settings import settings, EMAIL_CONFIRMATION, REGISTRATION_OPEN


if REGISTRATION_OPEN:
    ProfileBase = generics.ListCreateAPIView
else:
    ProfileBase = generics.ListAPIView


class ProfileList(ProfileBase):
    """
    Return profile of current authenticated user or return 401.

    ### POST

    Create a new user account.
    Sends a confirmation mail if if PROFILE_EMAL_CONFIRMATION setting is True.

    **Required Fields**:

     * username
     * email
     * password
     * password_confirmation

    ** Optional Fields **

     * first_name
     * last_name
     * about
     * gender
     * birth_date
     * address
     * city
     * country
    """
    authentication_classes = (TokenAuthentication, SessionAuthentication)
    model = Profile
    serializer_class = ProfileCreateSerializer
    # custom
    serializer_reader_class = ProfileOwnSerializer

    def get(self, request, *args, **kwargs):
        """ return profile of current user if authenticated otherwise 401 """
        serializer = self.serializer_reader_class
        if request.user.is_authenticated():
            return Response(serializer(request.user, context=self.get_serializer_context()).data)
        else:
            return Response({'detail': _('Authentication credentials were not provided')}, status=401)

    def perform_create(self, serializer):
        """
        Send email confirmation according to configuration
        """
        serializer.save()
        serializer.instance.needs_confirmation()

profile_list = ProfileList.as_view()


class ProfileDetail(generics.RetrieveUpdateAPIView):
    """
    Retrieve specified profile.

    ### PUT & PATCH

    Update profile.

    **Permissions**: only profile owner can edit.

    **Editable fields**

     * first_name
     * last_name
     * about
     * gender
     * birth_date
     * address
     * city
     * country
    """
    authentication_classes = (TokenAuthentication, SessionAuthentication)
    permission_classes = (IsAuthenticatedOrReadOnly, IsProfileOwner)
    queryset = Profile.objects.all()
    serializer_class = ProfileSerializer
    lookup_field = 'username'

profile_detail = ProfileDetail.as_view()


# ------ User Nodes ------ #


if 'nodeshot.core.nodes' in settings.INSTALLED_APPS:
    from nodeshot.core.base.utils import Hider
    from nodeshot.core.nodes.views import NodeList

    class UserNodes(NodeList):
        """
        Retrieve list of nodes of the specified user

        Parameters:

         * `search=<word>`: search <word> in name of nodes of specified layer
         * `limit=<n>`: specify number of items per page (defaults to 40)
         * `limit=0`: turns off pagination
        """
        def get_queryset(self):
            try:
                self.user = Profile.objects.get(username=self.kwargs['username'])
            except Profile.DoesNotExist:
                raise Http404(_('User not found'))
            return super(UserNodes, self).get_queryset().filter(user_id=self.user.id)
        # remove post method
        post = Hider()

    user_nodes = UserNodes.as_view()


# ------ User Social Links ------ #


class SocialLinkMixin(object):
    """
    Current user queryset
    """
    queryset = SocialLink.objects.select_related('user').only(
        'id', 'user', 'user__username', 'url', 'description', 'added', 'updated'
    )

    def get_queryset(self):
        try:
            self.user = Profile.objects.only('id', 'username').get(username=self.kwargs['username'])
        except Profile.DoesNotExist:
            raise Http404(_('User not found'))
        return super(SocialLinkMixin, self).get_queryset().filter(user_id=self.user.id)


class UserSocialLinksList(SocialLinkMixin, generics.ListCreateAPIView):
    """
    Get social links of a user

    ### POST

    Insert new social link. Profile owner only.
    """
    authentication_classes = (TokenAuthentication, SessionAuthentication)
    permission_classes = (IsAuthenticatedOrReadOnly, IsProfileOwner)
    serializer_class = SocialLinkSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

user_social_links_list = UserSocialLinksList.as_view()


class UserSocialLinksDetail(SocialLinkMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    Get specified social link

    ### PUT & PATCH

    Edit existing social link. Profile owner only.

    ### DELETE

    Delete social link. Profile owner only.
    """
    authentication_classes = (TokenAuthentication, SessionAuthentication)
    permission_classes = (IsAuthenticatedOrReadOnly, IsProfileOwner)
    serializer_class = SocialLinkSerializer

user_social_links_detail = UserSocialLinksDetail.as_view()


# ------ Account ------ #


class AccountLogin(generics.GenericAPIView):
    """
    Log in

    **Parameters**:

     * username
     * password
     * remember
    """
    authentication_classes = (TokenAuthentication, SessionAuthentication)
    permission_classes = (IsNotAuthenticated, )
    serializer_class = LoginSerializer

    def post(self, request, format=None):
        # zhuguangjun test for cookie
        print ">>>> start login"
        if request.session.test_cookie_worked():
            print ">>>> TEST COOKIE WORKED!"
            request.session.delete_test_cookie()

        """ authenticate """
        serializer = self.serializer_class(data=request.data)

        if serializer.is_valid():
            login(request, serializer.instance)

            if request.data.get('remember'):
                # TODO: remember configurable
                request.session.set_expiry(60 * 60 * 24 * 7 * 3)
            else:
                request.session.set_expiry(0)

            return Response({
                'detail': _(u'Logged in successfully'),
                'user': ProfileOwnSerializer(serializer.instance,
                                             context={'request': request}).data
            })
        return Response(serializer.errors, status=400)

account_login = AccountLogin.as_view()


class AccountLogout(APIView):
    """
    Log out
    """
    authentication_classes = (TokenAuthentication, SessionAuthentication)
    permission_classes = (IsAuthenticated, )

    def post(self, request, format=None):
        """ clear session """
        logout(request)
        return Response({'detail': _(u'Logged out successfully')})

account_logout = AccountLogout.as_view()


class AccountDetail(generics.RetrieveAPIView):
    """
    Retrieve profile of current user or return 401 if not authenticated.
    """
    authentication_classes = (TokenAuthentication, SessionAuthentication)
    permission_classes = (IsAuthenticated, )
    serializer_class = AccountSerializer

    def get_object(self):
        return self.request.user

account_detail = AccountDetail.as_view()


# ------ Account Password ------ #


class AccountPassword(generics.GenericAPIView):
    """
    Change password of the current user.

    **Accepted parameters:**

     * current_password
     * password1
     * password2
    """
    authentication_classes = (TokenAuthentication, SessionAuthentication)
    permission_classes = (IsAuthenticated,)
    serializer_class = ChangePasswordSerializer

    def post(self, request, format=None):
        """ validate password change operation and return result """
        serializer_class = self.get_serializer_class()
        serializer = serializer_class(data=request.data, instance=request.user)

        if serializer.is_valid():
            serializer.save()
            return Response({'detail': _(u'Password successfully changed')})

        return Response(serializer.errors, status=400)

account_password_change = AccountPassword.as_view()


class PasswordResetRequestKey(generics.GenericAPIView):
    """
    Sends an email to the user email address with a link to reset his password.

    **TODO:** the key should be sent via push notification too.

    **Accepted parameters:**

     * email
    """
    authentication_classes = (TokenAuthentication, SessionAuthentication)
    permission_classes = (IsNotAuthenticated, )
    serializer_class = ResetPasswordSerializer

    def post(self, request, format=None):
        # init form with POST data
        serializer = self.serializer_class(data=request.data)
        # validate
        if serializer.is_valid():
            serializer.save()
            return Response({
                'detail': _(u'We just sent you the link with which you will able to reset your password at %s') % request.data.get('email')
            })
        # in case of errors
        return Response(serializer.errors, status=400)

account_password_reset_request_key = PasswordResetRequestKey.as_view()


class PasswordResetFromKey(generics.GenericAPIView):
    """
    Reset password from key.

    **The key must be part of the URL**!

    **Accepted parameters:**

     * password1
     * password2
    """
    authentication_classes = (TokenAuthentication, SessionAuthentication)
    permission_classes = (IsNotAuthenticated, )
    serializer_class = ResetPasswordKeySerializer

    def post(self, request, uidb36, key, format=None):
        # pull out user
        try:
            uid_int = base36_to_int(uidb36)
            password_reset_key = PasswordReset.objects.get(user_id=uid_int, temp_key=key, reset=False)
        except (ValueError, PasswordReset.DoesNotExist, AttributeError):
            return Response({'errors': _(u'Key Not Found')}, status=404)

        serializer = ResetPasswordKeySerializer(
            data=request.data,
            instance=password_reset_key
        )

        # validate
        if serializer.is_valid():
            serializer.save()
            return Response({'detail': _(u'Password successfully changed.')})
        # in case of errors
        return Response(serializer.errors, status=400)

account_password_reset_from_key = PasswordResetFromKey.as_view()


# ------ Account Email ------ #


if EMAIL_CONFIRMATION:
    from .models import EmailAddress, EmailConfirmation

    class AccountEmailList(generics.ListCreateAPIView):
        """
        Get email addresses of current authenticated user.

        ### POST

        Add new email address.
        """
        authentication_classes = (TokenAuthentication, SessionAuthentication)
        permission_classes = (IsAuthenticated,)
        serializer_class = EmailSerializer
        model = EmailAddress

        def get_queryset(self):
            return self.model.objects.filter(user=self.request.user)

        def perform_create(self, serializer):
            """ Send email confirmation """
            serializer.save(user=self.request.user)
            EmailConfirmation.objects.send_confirmation(serializer.instance)

    account_email_list = AccountEmailList.as_view()

    # noqa
    class AccountEmailDetail(generics.RetrieveUpdateDestroyAPIView):
        """
        Get specified email object.

        ### PUT & PATCH

        Make primary.

        ### DELETE

        Delete email address
        """
        authentication_classes = (TokenAuthentication, SessionAuthentication)
        permission_classes = (IsAuthenticated,)
        serializer_class = EmailEditSerializer
        model = EmailAddress

        def get_queryset(self):
            return self.model.objects.filter(user=self.request.user)

        def delete(self, request, *args, **kwargs):
            """ can't delete if only 1 email address """
            if self.get_object().primary:
                return Response({'detail': _("You can't delete your primary address")}, status=400)
            elif EmailAddress.objects.filter(user=request.user).count() <= 1:
                return Response({'detail': _("You can't delete your only email address")}, status=400)
            return self.destroy(request, *args, **kwargs)

    account_email_detail = AccountEmailDetail.as_view()

    # noqa
    class ResendEmailConfirmation(APIView):
        """ Resend email confirmation """
        authentication_classes = (TokenAuthentication, SessionAuthentication)
        permission_classes = (IsAuthenticated,)

        def post(self, request, *args, **kwargs):
            try:
                email_address = EmailAddress.objects.get(user=request.user, pk=kwargs.get('pk', None))
            except EmailAddress.DoesNotExist:
                return Response({'detail': _('Not Found')}, status=404)

            if email_address.verified:
                return Response({'detail': _('Email address %s already verified' % email_address.email)}, status=400)

            EmailConfirmation.objects.send_confirmation(email_address)
            return Response({'detail': _('Email confirmation sent to %s' % email_address.email)})

    account_email_resend_confirmation = ResendEmailConfirmation.as_view()
