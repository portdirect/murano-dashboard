/*    Copyright (c) 2013 Mirantis, Inc.

    Licensed under the Apache License, Version 2.0 (the "License"); you may
    not use this file except in compliance with the License. You may obtain
    a copy of the License at

         http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
    License for the specific language governing permissions and limitations
    under the License.
*/
$(function() {
    function check_mixed_mode(){
        var checked = $("input[id*='mixedModeAuth']").prop('checked')
        if ( checked === true) {
            $("label[for*='saPassword']").parent().css({'display': 'inline-block'});
        } else if (checked === false) {
            $("label[for*='saPassword']").parent().css({'display': 'none'});
        }
    }

    $("input[id*='mixedModeAuth']").change(check_mixed_mode);
    check_mixed_mode();
    $(".checkbox").css({'float': 'left', 'width': 'auto', 'margin-right': '10px'})
});
